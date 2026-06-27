import * as vscode from "vscode";
import { OrchestratorClient } from "./services/orchestratorClient";
import { PreviewApplyProvider } from "./services/applyProvider";
import { NoopSandboxRunner } from "./services/placeholders";
import { BenchOption, ChatMessage, WorkspaceContext } from "./types";

export function activate(context: vscode.ExtensionContext): void {
  const provider = new BenchChatViewProvider(context);

  context.subscriptions.push(
    vscode.window.registerWebviewViewProvider(BenchChatViewProvider.viewType, provider, {
      webviewOptions: {
        retainContextWhenHidden: true
      }
    }),
    vscode.commands.registerCommand("bench.openChat", async () => {
      await vscode.commands.executeCommand("workbench.view.extension.bench");
      await vscode.commands.executeCommand("bench.chatView.focus");
    }),
    vscode.commands.registerCommand("bench.newFeatureChat", async () => {
      await vscode.commands.executeCommand("workbench.view.extension.bench");
      provider.reset();
    })
  );
}

export function deactivate(): void {}

class BenchChatViewProvider implements vscode.WebviewViewProvider {
  static readonly viewType = "bench.chatView";

  private view?: vscode.WebviewView;
  private readonly detailsPanels = new Map<string, vscode.WebviewPanel>();
  private readonly orchestrator = new OrchestratorClient();
  private readonly sandboxRunner = new NoopSandboxRunner();
  private readonly applyProvider = new PreviewApplyProvider();
  private messages: ChatMessage[] = [
    {
      id: "welcome",
      role: "assistant",
      content: "Describe the change you want to make. Bench will compare a few implementation paths and let you preview one in the editor before applying it."
    }
  ];
  private options: BenchOption[] = [];
  private selectedOptionId?: string;
  private activeOptionsMessageId?: string;

  constructor(private readonly context: vscode.ExtensionContext) {}

  resolveWebviewView(webviewView: vscode.WebviewView): void {
    this.view = webviewView;
    webviewView.webview.options = {
      enableScripts: true,
      localResourceRoots: [this.context.extensionUri]
    };
    webviewView.webview.html = this.getChatHtml(webviewView.webview);

    webviewView.webview.onDidReceiveMessage(async (message: WebviewMessage) => {
      switch (message.type) {
        case "ready":
          this.postState();
          break;
        case "askFeature":
          await this.askFeature(message.text ?? "");
          break;
        case "viewMetrics":
          this.toggleDetailsPanel(message.messageId, message.optionId, "metrics");
          break;
        case "viewCode":
          this.toggleDetailsPanel(message.messageId, message.optionId, "code");
          break;
        case "selectOption":
          await this.selectOption(message.messageId, message.optionId);
          break;
        case "applySelected":
          await this.applySelected(message.messageId, message.optionId);
          break;
        case "rejectSelected":
          await this.rejectSelected(message.messageId, message.optionId);
          break;
      }
    });
  }

  reset(): void {
    this.messages = [
      {
        id: createId("assistant"),
        role: "assistant",
        content: "Fresh chat ready. What feature should we design and compare?"
      }
    ];
    this.options = [];
    this.selectedOptionId = undefined;
    this.activeOptionsMessageId = undefined;
    this.postState();
  }

  private async askFeature(text: string): Promise<void> {
    const prompt = text.trim();
    if (!prompt) {
      return;
    }

    this.messages.push({ id: createId("user"), role: "user", content: prompt });
    this.postState({ loading: true, notice: "Drafting implementation options..." });

    try {
      const workspaceContext = getWorkspaceContext();
      const result = await this.orchestrator.generateFeatureOptions(prompt, workspaceContext);
      this.options = await this.sandboxRunner.run(result.options);
      this.selectedOptionId = undefined;
      const messageId = createId("assistant");
      this.activeOptionsMessageId = messageId;
      this.messages.push({
        id: messageId,
        role: "assistant",
        content: result.message || `I found ${this.options.length} implementation options. Compare the tradeoffs, preview one in code, then apply the version you want to keep.`,
        options: cloneOptions(this.options),
        sourcePrompt: prompt
      });
      this.postState();
    } catch (error) {
      this.options = buildFallbackSuggestions(prompt);
      const messageId = createId("assistant");
      this.activeOptionsMessageId = messageId;
      this.messages.push({
        id: messageId,
        role: "assistant",
        content: `I could not reach the Bench orchestrator, so I loaded local demo options instead. ${formatError(error)}`,
        options: cloneOptions(this.options),
        sourcePrompt: prompt
      });
      this.postState({ error: formatError(error) });
    }
  }

  private async selectOption(messageId?: string, optionId?: string): Promise<void> {
    const option = this.findOption(messageId, optionId);
    if (!option || !messageId || !optionId) {
      return;
    }

    try {
      const sessionId = buildSessionId(messageId, optionId);
      const preview = await this.applyProvider.preview(sessionId, option, getWorkspaceContext());
      this.applySessionState(messageId, optionId, "previewed", preview.summary);
      for (const deactivatedSessionId of preview.deactivatedSessionIds ?? []) {
        const parsed = parseSessionId(deactivatedSessionId);
        if (parsed) {
          this.applySessionState(parsed.messageId, parsed.optionId, "idle");
        }
      }
      this.selectedOptionId = option.id;
      void vscode.window.showInformationMessage(
        `Preview loaded for "${option.title}". Review the inline draft in the editor, then apply or reject from the selected card.`
      );
      this.postState({ notice: preview.summary });
    } catch (error) {
      const errorMessage = `Bench could not preview "${option.title}". ${formatError(error)}`;
      void vscode.window.showErrorMessage(errorMessage);
      this.postState({ error: formatError(error) });
    }
  }

  private async applySelected(messageId?: string, optionId?: string): Promise<void> {
    if (!messageId || !optionId) {
      return;
    }
    try {
      await this.clearSiblingPreviews(messageId, optionId);
      const result = await this.applyProvider.applySelected(buildSessionId(messageId, optionId));
      if (!result) {
        return;
      }

      void this.rememberDecision(messageId, optionId);
      this.finalizeAppliedMessage(messageId, optionId, result.summary);
      this.selectedOptionId = result.optionId;
      this.closeMessageDetailsPanels(messageId);
      void vscode.window.showInformationMessage(result.summary);
      this.postState({ notice: result.summary });
    } catch (error) {
      const errorMessage = formatError(error);
      void vscode.window.showErrorMessage(errorMessage);
      this.postState({ error: errorMessage });
    }
  }

  private async rejectSelected(messageId?: string, optionId?: string): Promise<void> {
    if (!messageId || !optionId) {
      return;
    }
    const summary = await this.applyProvider.rejectSelected(buildSessionId(messageId, optionId));
    if (!summary) {
      return;
    }

    this.applySessionState(messageId, optionId, "idle");
    this.closeOptionDetailsPanel(messageId, optionId);
    this.selectedOptionId = undefined;
    void vscode.window.showInformationMessage(summary);
    this.postState({ notice: summary });
  }

  private toggleDetailsPanel(messageId?: string, optionId?: string, tab: "metrics" | "code" = "metrics"): void {
    const option = this.findOption(messageId, optionId);
    if (!option || !optionId || !messageId) {
      return;
    }

    const panelKey = buildSessionId(messageId, optionId);
    const existingPanel = this.detailsPanels.get(panelKey);
    if (existingPanel) {
      existingPanel.dispose();
      return;
    }

    const panel = vscode.window.createWebviewPanel(
      "bench.metrics",
      `Bench: ${option.title}`,
      vscode.ViewColumn.Beside,
      {
        enableScripts: true,
        retainContextWhenHidden: true
      }
    );
    this.detailsPanels.set(panelKey, panel);
    panel.onDidDispose(() => {
      this.detailsPanels.delete(panelKey);
    });
    panel.webview.html = this.getDetailsHtml(panel.webview, option, tab);
    panel.reveal(vscode.ViewColumn.Beside, false);
  }

  private postState(extra: Partial<WebviewState> = {}): void {
    this.view?.webview.postMessage({
      type: "state",
        state: {
        messages: this.messages,
        options: this.options,
        selectedOptionId: this.selectedOptionId,
        loading: false,
        ...extra
      } satisfies WebviewState
    });
  }

  private syncActiveOptionsMessage(): void {
    if (!this.activeOptionsMessageId) {
      return;
    }

    this.messages = this.messages.map((message) => (
      message.id === this.activeOptionsMessageId
        ? { ...message, options: cloneOptions(this.options) }
        : message
    ));
  }

  private findOption(messageId?: string, optionId?: string): BenchOption | undefined {
    if (!messageId || !optionId) {
      return undefined;
    }
    const message = this.messages.find((item) => item.id === messageId);
    return message?.options?.find((option) => option.id === optionId);
  }

  private applySessionState(
    messageId: string,
    optionId: string,
    applyState: BenchOption["applyState"],
    applySummary?: string
  ): void {
    this.messages = this.messages.map((message) => {
      if (message.id !== messageId || !message.options) {
        return message;
      }
      return {
        ...message,
        options: message.options.map((option) => (
          option.id === optionId
            ? { ...option, selected: applyState === "previewed" || applyState === "applied", applyState, applySummary }
            : option
        ))
      };
    });

    if (this.activeOptionsMessageId === messageId) {
      this.options = (this.messages.find((message) => message.id === messageId)?.options ?? []).map((option) => ({
        ...option,
        metrics: { ...option.metrics },
        tradeoffs: [...option.tradeoffs]
      }));
    }
  }

  private closeOptionDetailsPanel(messageId: string, optionId: string): void {
    this.detailsPanels.get(buildSessionId(messageId, optionId))?.dispose();
  }

  private closeMessageDetailsPanels(messageId: string): void {
    for (const [panelKey, panel] of this.detailsPanels) {
      if (panelKey.startsWith(`${messageId}::`)) {
        panel.dispose();
      }
    }
  }

  private async clearSiblingPreviews(messageId: string, appliedOptionId: string): Promise<void> {
    const message = this.messages.find((item) => item.id === messageId);
    if (!message?.options) {
      return;
    }

    for (const option of message.options) {
      if (option.id === appliedOptionId) {
        continue;
      }
      const sessionId = buildSessionId(messageId, option.id);
      if (!this.applyProvider.hasPendingSession(sessionId)) {
        continue;
      }
      await this.applyProvider.rejectSelected(sessionId);
      this.applySessionState(messageId, option.id, "idle");
    }
  }


  private async rememberDecision(messageId: string, optionId: string): Promise<void> {
    const message = this.messages.find((item) => item.id === messageId);
    if (!message?.options?.length) {
      return;
    }

    const sourcePrompt = message.sourcePrompt ?? this.findNearestUserPrompt(messageId);
    if (!sourcePrompt) {
      return;
    }

    try {
      await this.orchestrator.rememberFeatureDecision(sourcePrompt, optionId, message.options);
    } catch (error) {
      this.postState({ error: `Applied, but Bench could not save the preference memory. ${formatError(error)}` });
    }
  }

  private findNearestUserPrompt(messageId: string): string | undefined {
    const messageIndex = this.messages.findIndex((item) => item.id === messageId);
    for (let index = messageIndex - 1; index >= 0; index -= 1) {
      const candidate = this.messages[index];
      if (candidate.role === "user") {
        return candidate.content;
      }
    }
    return undefined;
  }

  private finalizeAppliedMessage(messageId: string, optionId: string, summary: string): void {
    this.messages = this.messages.map((message) => {
      if (message.id !== messageId || !message.options) {
        return message;
      }

      const appliedOption = message.options.find((option) => option.id === optionId);
      if (!appliedOption) {
        return message;
      }

      return {
        ...message,
        options: undefined,
        appliedOptionId: appliedOption.id,
        appliedOptionTitle: appliedOption.title,
        appliedSummary: summary
      };
    });

    if (this.activeOptionsMessageId === messageId) {
      this.options = [];
    }
  }

  private getChatHtml(webview: vscode.Webview): string {
    const nonce = createNonce();
    const cspSource = webview.cspSource;

    return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src ${cspSource} data:; style-src 'unsafe-inline' ${cspSource}; script-src 'nonce-${nonce}';">
<title>Bench Chat</title>
<style>
  :root {
    color-scheme: dark;
    --bg: var(--vscode-sideBar-background);
    --panel: var(--vscode-editor-background);
    --card: color-mix(in srgb, var(--vscode-sideBar-background) 72%, var(--vscode-editor-foreground) 4%);
    --card-hover: color-mix(in srgb, var(--vscode-sideBar-background) 66%, var(--vscode-editor-foreground) 7%);
    --border: color-mix(in srgb, var(--vscode-panel-border) 78%, transparent);
    --text: var(--vscode-foreground);
    --muted: var(--vscode-descriptionForeground);
    --accent: var(--vscode-focusBorder);
    --accent-soft: color-mix(in srgb, var(--vscode-focusBorder) 18%, transparent);
    --button: var(--vscode-button-background);
    --button-hover: var(--vscode-button-hoverBackground);
    --good: #73c991;
    --warn: #cca700;
  }
  * { box-sizing: border-box; }
  body { margin: 0; font-family: var(--vscode-font-family); color: var(--text); background: var(--bg); font-size: 13px; }
  .app { height: 100vh; display: grid; grid-template-rows: auto 1fr auto; }
  header { padding: 14px 16px 12px; border-bottom: 1px solid var(--border); background: var(--panel); }
  .brand { display: flex; align-items: center; gap: 9px; font-weight: 650; letter-spacing: .01em; }
  .bolt { width: 22px; height: 22px; border-radius: 5px; display: grid; place-items: center; color: var(--vscode-badge-foreground); background: var(--vscode-badge-background); font-weight: 700; }
  .sub { margin-top: 6px; font-size: 12px; color: var(--muted); line-height: 1.42; max-width: 58ch; }
  main { overflow: auto; padding: 12px 12px 14px; display: flex; flex-direction: column; gap: 10px; scrollbar-gutter: stable; }
  .msg { line-height: 1.45; color: var(--text); }
  .msg.assistant { border-left: 2px solid var(--border); padding-left: 10px; color: color-mix(in srgb, var(--text) 86%, var(--muted)); }
  .msg.user { align-self: flex-end; max-width: 92%; border: 1px solid var(--border); background: var(--card); border-radius: 8px; padding: 9px 10px; }
  .msg.system { color: var(--muted); font-size: 12px; }
  .role { display: none; }
  .cards { display: flex; flex-direction: column; gap: 8px; margin-top: 11px; }
  .card { border: 1px solid var(--border); background: var(--card); border-radius: 7px; padding: 11px; transition: background .12s ease, border-color .12s ease; }
  .card:hover { background: var(--card-hover); }
  .card.selected { border-color: var(--accent); background: color-mix(in srgb, var(--card) 82%, var(--accent) 8%); }
  .card.recommended { border-color: var(--accent); box-shadow: 0 0 0 1px var(--accent); }
  .cardTop { display: flex; justify-content: space-between; gap: 8px; align-items: start; }
  .title { font-weight: 650; line-height: 1.28; }
  .summary { margin-top: 6px; color: var(--muted); font-size: 12px; line-height: 1.42; }
  .selectedBadge { font-size: 10px; color: var(--vscode-badge-foreground); background: var(--vscode-badge-background); border-radius: 999px; padding: 2px 7px; font-weight: 650; white-space: nowrap; }
  .recommendation { margin-top: 8px; color: var(--text); font-size: 12px; line-height: 1.42; border-left: 2px solid var(--accent); padding-left: 8px; }
  .actions { display: flex; gap: 7px; flex-wrap: wrap; margin-top: 11px; }
  button { border: 1px solid var(--vscode-button-border, transparent); color: var(--vscode-button-foreground); background: var(--button); border-radius: 5px; padding: 6px 10px; cursor: pointer; font: inherit; font-size: 12px; min-height: 28px; }
  button.secondary { background: transparent; color: var(--text); border-color: var(--border); }
  button:hover { background: var(--button-hover); }
  button.secondary:hover { background: var(--vscode-list-hoverBackground); }
  button:disabled { opacity: .58; cursor: default; }
  .composer { border-top: 1px solid var(--border); padding: 10px 12px 12px; display: grid; grid-template-columns: 1fr auto; gap: 8px; background: var(--panel); }
  textarea { resize: none; min-height: 44px; max-height: 140px; border-radius: 7px; border: 1px solid var(--border); background: var(--vscode-input-background); color: var(--text); padding: 10px; font: inherit; line-height: 1.4; }
  textarea:focus { outline: 1px solid var(--accent); outline-offset: -1px; }
  .status { grid-column: 1 / -1; min-height: 18px; font-size: 12px; color: var(--muted); }
  .error { color: var(--vscode-errorForeground); }
  .empty { color: var(--muted); font-size: 12px; padding: 12px; border: 1px dashed var(--border); border-radius: 8px; }
</style>
</head>
<body>
  <div class="app">
    <header>
      <div class="brand"><span class="bolt">B</span><span>Bench</span></div>
      <div class="sub">Compare implementation paths, preview the best fit, and apply it when it reads right.</div>
    </header>
    <main id="messages"></main>
    <form class="composer" id="form">
      <textarea id="input" placeholder="Describe a change..."></textarea>
      <button id="send" type="submit">Send</button>
      <div class="status" id="status"></div>
    </form>
  </div>
  <script nonce="${nonce}">
    const vscode = acquireVsCodeApi();
    const messagesEl = document.getElementById('messages');
    const formEl = document.getElementById('form');
    const inputEl = document.getElementById('input');
    const sendEl = document.getElementById('send');
    const statusEl = document.getElementById('status');
    let state = { messages: [], options: [], loading: false };

    window.addEventListener('message', (event) => {
      if (event.data.type !== 'state') return;
      state = event.data.state;
      render();
    });

    formEl.addEventListener('submit', (event) => {
      event.preventDefault();
      const text = inputEl.value.trim();
      if (!text || state.loading) return;
      vscode.postMessage({ type: 'askFeature', text });
      inputEl.value = '';
    });

    messagesEl.addEventListener('click', (event) => {
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      const card = target.closest('[data-option-id]');
      const optionId = card?.getAttribute('data-option-id');
      const messageId = card?.getAttribute('data-message-id');
      const action = target.getAttribute('data-action');
      if (!optionId || !action) return;
      vscode.postMessage({ type: action, optionId, messageId });
    });

    function render() {
      sendEl.disabled = Boolean(state.loading);
      statusEl.textContent = state.loading ? 'Drafting implementation options...' : (state.notice || '');
      statusEl.className = state.error ? 'status error' : 'status';
      messagesEl.innerHTML = '';

      for (const message of state.messages) {
        const node = document.createElement('section');
        node.className = 'msg ' + message.role;
        node.innerHTML = '<div class="role">' + escapeHtml(message.role) + '</div><div>' + escapeHtml(message.content) + '</div>';
        if (message.appliedOptionTitle) {
          node.appendChild(renderAppliedMessage(message));
        } else if (message.options?.length) {
          node.appendChild(renderCards(message.id, message.options));
        }
        messagesEl.appendChild(node);
      }

      if (state.messages.length === 0) {
        messagesEl.innerHTML = '<div class="empty">Start with a feature request.</div>';
      }

      messagesEl.scrollTop = messagesEl.scrollHeight;
    }

    function renderCards(messageId, options) {
      const wrap = document.createElement('div');
      wrap.className = 'cards';
      for (const option of options) {
        const card = document.createElement('article');
        card.className = 'card' + (option.selected ? ' selected' : '') + (option.recommended ? ' recommended' : '');
        card.setAttribute('data-option-id', option.id);
        card.setAttribute('data-message-id', messageId);
        card.innerHTML = \`
          <div class="cardTop">
            <div>
              <div class="title">\${escapeHtml(option.title)}</div>
              <div class="summary">\${escapeHtml(option.summary)}</div>
              \${option.recommended && option.recommendationReason ? '<div class="recommendation">' + escapeHtml(option.recommendationReason) + '</div>' : ''}
            </div>
            \${option.applyState === 'applied' ? '<span class="selectedBadge">Applied</span>' : option.selected ? '<span class="selectedBadge">Preview Ready</span>' : option.recommended ? '<span class="selectedBadge">Recommended</span>' : ''}
          </div>
          \${option.applyState === 'applied'
            ? '<div class="summary">This implementation has been applied and locked in.</div>'
            : \`<div class="actions">
                <button class="secondary" data-action="viewMetrics" type="button">Details</button>
                <button data-action="\${option.selected ? 'rejectSelected' : 'selectOption'}" type="button">\${option.selected ? 'Close Preview' : 'Preview'}</button>
                \${option.selected ? '<button data-action="applySelected" type="button">Accept</button>' : ''}
              </div>\`}\`;
        wrap.appendChild(card);
      }
      return wrap;
    }

    function renderAppliedMessage(message) {
      const wrap = document.createElement('div');
      wrap.className = 'cards';
      const card = document.createElement('article');
      card.className = 'card selected';
      card.innerHTML = \`
        <div class="cardTop">
          <div>
            <div class="title">Applied: \${escapeHtml(message.appliedOptionTitle)}</div>
            <div class="summary">\${escapeHtml(message.appliedSummary || 'This implementation has been applied and this option set is now locked.')}</div>
          </div>
          <span class="selectedBadge">Applied</span>
        </div>\`;
      wrap.appendChild(card);
      return wrap;
    }

    function escapeHtml(value) {
      return String(value ?? '').replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' }[char]));
    }

    vscode.postMessage({ type: 'ready' });
  </script>
</body>
</html>`;
  }

  private getDetailsHtml(webview: vscode.Webview, option: BenchOption, activeTab: "metrics" | "code"): string {
    const nonce = createNonce();
    const optionJson = JSON.stringify(option).replace(/</g, "\\u003c");

    return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline' ${webview.cspSource}; script-src 'nonce-${nonce}';">
<title>Bench Details</title>
<style>
  :root {
    color-scheme: dark;
    --panel: var(--vscode-editor-background);
    --card: color-mix(in srgb, var(--vscode-sideBar-background) 72%, var(--vscode-editor-foreground) 4%);
    --card-hover: color-mix(in srgb, var(--vscode-sideBar-background) 66%, var(--vscode-editor-foreground) 7%);
    --border: color-mix(in srgb, var(--vscode-panel-border) 78%, transparent);
    --text: var(--vscode-foreground);
    --muted: var(--vscode-descriptionForeground);
    --accent: var(--vscode-focusBorder);
    --accent-soft: color-mix(in srgb, var(--vscode-focusBorder) 18%, transparent);
    --button: var(--vscode-button-background);
    --button-hover: var(--vscode-button-hoverBackground);
  }
  * { box-sizing: border-box; }
  body { margin: 0; padding: 18px 22px; font-family: var(--vscode-font-family); color: var(--text); background: var(--panel); font-size: 13px; }
  h1 { font-size: 20px; line-height: 1.25; margin: 0; font-weight: 650; max-width: 760px; }
  .summary { color: var(--muted); margin-top: 9px; line-height: 1.5; max-width: 760px; }
  .tabs { display: flex; gap: 6px; margin: 20px 0 18px; border-bottom: 1px solid var(--border); padding-bottom: 10px; }
  button { border: 1px solid var(--border); background: transparent; color: var(--text); border-radius: 5px; padding: 7px 11px; cursor: pointer; font: inherit; min-height: 30px; }
  button:hover { background: var(--vscode-list-hoverBackground); }
  button.active { color: var(--vscode-button-foreground); border-color: var(--button); background: var(--button); font-weight: 650; }
  .panel { display: none; max-width: 980px; }
  .panel.active { display: block; }
  .metric { display: grid; grid-template-columns: minmax(120px, 170px) 1fr 38px; gap: 12px; align-items: center; margin: 12px 0; }
  .metric strong { font-weight: 600; }
  .bar { height: 8px; background: color-mix(in srgb, var(--muted) 18%, transparent); border-radius: 999px; overflow: hidden; }
  .bar span { display: block; height: 100%; background: var(--accent); }
  .card { border: 1px solid var(--border); border-radius: 7px; background: var(--card); padding: 14px; margin: 12px 0; }
  pre { white-space: pre-wrap; overflow: auto; border: 1px solid var(--border); border-radius: 7px; background: var(--vscode-textCodeBlock-background); padding: 16px; line-height: 1.55; font-size: 13px; }
  ul { padding-left: 20px; }
  li { margin: 6px 0; }
</style>
</head>
<body>
  <h1 id="title"></h1>
  <div class="summary" id="summary"></div>
  <div class="tabs">
    <button id="metricsTab" data-tab="metrics">Metrics</button>
    <button id="codeTab" data-tab="code">Code</button>
    <button id="planTab" data-tab="plan">Plan</button>
  </div>
  <section id="metrics" class="panel"></section>
  <section id="code" class="panel"><pre id="codeBlock"></pre></section>
  <section id="plan" class="panel">
    <div class="card"><strong>Implementation Plan</strong><p id="implementationPlan"></p></div>
    <div class="card"><strong>Tradeoffs</strong><ul id="tradeoffs"></ul></div>
  </section>
  <script nonce="${nonce}">
    const option = ${optionJson};
    const activeTab = ${JSON.stringify(activeTab)};
    document.getElementById('title').textContent = option.title;
    document.getElementById('summary').textContent = option.summary;
    document.getElementById('codeBlock').textContent = option.generatedCode || 'No generated code returned.';
    document.getElementById('implementationPlan').textContent = option.implementationPlan || 'No plan returned.';
    document.getElementById('tradeoffs').innerHTML = (option.tradeoffs || []).map((item) => '<li>' + escapeHtml(item) + '</li>').join('');
    document.getElementById('metrics').innerHTML = Object.entries(option.metrics).map(([key, value]) => {
      const label = key.replace(/([A-Z])/g, ' $1').replace(/^./, (char) => char.toUpperCase());
      return '<div class="metric"><strong>' + escapeHtml(label) + '</strong><div class="bar"><span style="width:' + value + '%"></span></div><span>' + value + '</span></div>';
    }).join('') + '<div class="card">Metric values are provisional until connected to the sandbox runner.</div>';

    document.querySelector('.tabs').addEventListener('click', (event) => {
      if (!(event.target instanceof HTMLElement)) return;
      const tab = event.target.getAttribute('data-tab');
      if (tab) activate(tab);
    });
    activate(activeTab);

    function activate(tab) {
      document.querySelectorAll('.panel').forEach((node) => node.classList.toggle('active', node.id === tab));
      document.querySelectorAll('button[data-tab]').forEach((node) => node.classList.toggle('active', node.getAttribute('data-tab') === tab));
    }

    function escapeHtml(value) {
      return String(value ?? '').replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' }[char]));
    }
  </script>
</body>
</html>`;
  }
}

type WebviewMessage = {
  type: "ready" | "askFeature" | "viewMetrics" | "viewCode" | "selectOption" | "applySelected" | "rejectSelected";
  text?: string;
  optionId?: string;
  messageId?: string;
};

type WebviewState = {
  messages: ChatMessage[];
  options: BenchOption[];
  selectedOptionId?: string;
  loading: boolean;
  notice?: string;
  error?: string;
};

function getWorkspaceContext(): WorkspaceContext {
  const editor = vscode.window.activeTextEditor;
  if (!editor) {
    return {};
  }

  const selectedText = editor.selection.isEmpty ? undefined : editor.document.getText(editor.selection);
  const visibleText = editor.document.getText().slice(0, 6000);

  return {
    activeFileName: editor.document.fileName,
    languageId: editor.document.languageId,
    selectedText,
    visibleText
  };
}

function buildFallbackSuggestions(featureRequest: string): BenchOption[] {
  const slug = featureRequest.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 36) || "feature";
  return [
    {
      id: `${slug}-simple`,
      title: "Simple service-first implementation",
      summary: "Keep the feature small, explicit, and easy to revise after the first user test.",
      implementationPlan: "Add a focused service function, call it from the relevant command or UI handler, and keep state changes local until the behavior is proven.",
      tradeoffs: ["Fastest to review", "May need a later abstraction if the feature grows"],
      generatedCode: `// Simple option for: ${featureRequest}\nexport async function buildFeature() {\n  // TODO: wire the focused implementation here.\n  return { ok: true };\n}`,
      metrics: {
        readability: 92,
        simplicity: 90,
        speed: 72,
        memory: 81,
        maintainability: 88,
        testConfidence: 76
      },
      selected: false,
      applyState: "idle"
    },
    {
      id: `${slug}-modular`,
      title: "Modular provider-based implementation",
      summary: "Introduce replaceable providers now so later Docker and apply flows slot in cleanly.",
      implementationPlan: "Define provider interfaces for generation, metrics, sandbox execution, and apply behavior, then bind MVP implementations behind them.",
      tradeoffs: ["Best future flexibility", "Slightly more structure up front"],
      generatedCode: `// Modular option for: ${featureRequest}\nexport interface FeatureProvider {\n  run(input: string): Promise<unknown>;\n}\n\nexport class MvpFeatureProvider implements FeatureProvider {\n  async run(input: string): Promise<unknown> {\n    return { input, status: 'planned' };\n  }\n}`,
      metrics: {
        readability: 84,
        simplicity: 72,
        speed: 78,
        memory: 74,
        maintainability: 94,
        testConfidence: 83
      },
      selected: false,
      applyState: "idle"
    },
    {
      id: `${slug}-fast`,
      title: "Fast path with cached state",
      summary: "Bias toward responsiveness by caching generated state and minimizing repeated work.",
      implementationPlan: "Add a state cache keyed by request/context, render immediately from cache when present, and refresh async when new data arrives.",
      tradeoffs: ["Snappier UX", "Cache invalidation must be handled carefully later"],
      generatedCode: `// Fast option for: ${featureRequest}\nconst featureCache = new Map<string, unknown>();\n\nexport async function runFeature(key: string) {\n  if (featureCache.has(key)) return featureCache.get(key);\n  const value = { key, generatedAt: Date.now() };\n  featureCache.set(key, value);\n  return value;\n}`,
      metrics: {
        readability: 76,
        simplicity: 70,
        speed: 95,
        memory: 68,
        maintainability: 79,
        testConfidence: 71
      },
      selected: false,
      applyState: "idle"
    }
  ];
}

function cloneOptions(options: BenchOption[]): BenchOption[] {
  return options.map((option) => ({
    ...option,
    metrics: { ...option.metrics },
    tradeoffs: [...option.tradeoffs]
  }));
}

function buildSessionId(messageId: string, optionId: string): string {
  return `${messageId}::${optionId}`;
}

function parseSessionId(sessionId: string): { messageId: string; optionId: string } | undefined {
  const separatorIndex = sessionId.indexOf("::");
  if (separatorIndex === -1) {
    return undefined;
  }
  return {
    messageId: sessionId.slice(0, separatorIndex),
    optionId: sessionId.slice(separatorIndex + 2)
  };
}

function createId(prefix: string): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function createNonce(): string {
  const possible = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
  let text = "";
  for (let i = 0; i < 32; i += 1) {
    text += possible.charAt(Math.floor(Math.random() * possible.length));
  }
  return text;
}

function formatError(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}



