import * as vscode from "vscode";
import { CerebrasClient } from "./services/cerebrasClient";
import { MockMetricsProvider } from "./services/mockMetricsProvider";
import { NoopSandboxRunner, SelectionOnlyApplyProvider } from "./services/placeholders";
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
    }),
    vscode.commands.registerCommand("bench.setCerebrasApiKey", async () => {
      const value = await vscode.window.showInputBox({
        title: "Set Cerebras API Key",
        prompt: "Stored in VS Code SecretStorage for Bench.",
        password: true,
        ignoreFocusOut: true
      });

      if (value === undefined) {
        return;
      }

      if (!value.trim()) {
        await context.secrets.delete("bench.cerebrasApiKey");
        vscode.window.showInformationMessage("Bench Cerebras API key cleared.");
        return;
      }

      await context.secrets.store("bench.cerebrasApiKey", value.trim());
      vscode.window.showInformationMessage("Bench Cerebras API key saved.");
    })
  );
}

export function deactivate(): void {}

class BenchChatViewProvider implements vscode.WebviewViewProvider {
  static readonly viewType = "bench.chatView";

  private view?: vscode.WebviewView;
  private readonly cerebras: CerebrasClient;
  private readonly metricsProvider = new MockMetricsProvider();
  private readonly sandboxRunner = new NoopSandboxRunner();
  private readonly applyProvider = new SelectionOnlyApplyProvider();
  private messages: ChatMessage[] = [
    {
      id: "welcome",
      role: "assistant",
      content: "Tell me what feature you want to build. I will generate a few implementation options, attach mock metrics for now, and let you inspect the details before choosing one."
    }
  ];
  private options: BenchOption[] = [];
  private selectedOptionId?: string;

  constructor(private readonly context: vscode.ExtensionContext) {
    this.cerebras = new CerebrasClient(context.secrets);
  }

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
          this.showMetricsPanel(message.optionId, "metrics");
          break;
        case "viewCode":
          this.showMetricsPanel(message.optionId, "code");
          break;
        case "selectOption":
          await this.selectOption(message.optionId);
          break;
        case "setApiKey":
          await vscode.commands.executeCommand("bench.setCerebrasApiKey");
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
    this.postState();
  }

  private async askFeature(text: string): Promise<void> {
    const prompt = text.trim();
    if (!prompt) {
      return;
    }

    this.messages.push({ id: createId("user"), role: "user", content: prompt });
    this.postState({ loading: true, notice: "Asking Cerebras for implementation options..." });

    try {
      const workspaceContext = getWorkspaceContext();
      const suggestions = await this.cerebras.generateSuggestions(prompt, workspaceContext);
      const optionsWithMetrics = this.metricsProvider.attachMetrics(suggestions);
      this.options = await this.sandboxRunner.run(optionsWithMetrics);
      this.selectedOptionId = undefined;
      this.messages.push({
        id: createId("assistant"),
        role: "assistant",
        content: `I generated ${this.options.length} implementation options. Metrics are mocked in this MVP; Docker-backed measurements will plug into the same cards later.`,
        options: this.options
      });
      this.postState();
    } catch (error) {
      this.options = this.metricsProvider.attachMetrics(buildFallbackSuggestions(prompt));
      this.messages.push({
        id: createId("assistant"),
        role: "assistant",
        content: `I could not reach or parse Cerebras, so I loaded local demo options instead. ${formatError(error)}`,
        options: this.options
      });
      this.postState({ error: formatError(error) });
    }
  }

  private async selectOption(optionId?: string): Promise<void> {
    const option = this.options.find((candidate) => candidate.id === optionId);
    if (!option) {
      return;
    }

    this.options = this.options.map((candidate) => ({
      ...candidate,
      selected: candidate.id === option.id
    }));
    this.selectedOptionId = option.id;
    await this.applyProvider.select(option);
    this.messages.push({
      id: createId("system"),
      role: "system",
      content: `Selected "${option.title}". No files were changed.`
    });
    this.postState({ notice: `Selected ${option.title}. Workspace unchanged.` });
  }

  private showMetricsPanel(optionId?: string, tab: "metrics" | "code" = "metrics"): void {
    const option = this.options.find((candidate) => candidate.id === optionId);
    if (!option) {
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

    panel.webview.html = this.getDetailsHtml(panel.webview, option, tab);
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
    --card: var(--vscode-input-background);
    --border: var(--vscode-panel-border);
    --text: var(--vscode-foreground);
    --muted: var(--vscode-descriptionForeground);
    --accent: #8b6bff;
    --accent2: #19e3ff;
    --good: #73c991;
    --warn: #cca700;
  }
  * { box-sizing: border-box; }
  body { margin: 0; font-family: var(--vscode-font-family); color: var(--text); background: var(--bg); }
  .app { height: 100vh; display: grid; grid-template-rows: auto 1fr auto; }
  header { padding: 12px 14px; border-bottom: 1px solid var(--border); background: var(--panel); }
  .brand { display: flex; align-items: center; gap: 9px; font-weight: 700; }
  .bolt { width: 22px; height: 22px; border-radius: 6px; display: grid; place-items: center; color: #09090f; background: linear-gradient(120deg,var(--accent),var(--accent2)); }
  .sub { margin-top: 4px; font-size: 12px; color: var(--muted); line-height: 1.35; }
  main { overflow: auto; padding: 12px; display: flex; flex-direction: column; gap: 12px; }
  .msg { border: 1px solid var(--border); background: var(--panel); border-radius: 8px; padding: 10px; line-height: 1.45; }
  .msg.user { background: color-mix(in srgb, var(--accent) 15%, var(--panel)); }
  .msg.system { color: var(--muted); font-size: 12px; }
  .role { font-size: 11px; color: var(--muted); margin-bottom: 4px; text-transform: uppercase; letter-spacing: .04em; }
  .cards { display: flex; flex-direction: column; gap: 9px; margin-top: 10px; }
  .card { border: 1px solid var(--border); background: var(--card); border-radius: 8px; padding: 10px; }
  .card.selected { border-color: var(--accent2); box-shadow: 0 0 0 1px color-mix(in srgb, var(--accent2) 35%, transparent); }
  .cardTop { display: flex; justify-content: space-between; gap: 8px; align-items: start; }
  .title { font-weight: 700; line-height: 1.25; }
  .summary { margin-top: 5px; color: var(--muted); font-size: 12px; line-height: 1.4; }
  .selectedBadge { font-size: 10px; color: #09090f; background: linear-gradient(120deg,var(--accent),var(--accent2)); border-radius: 999px; padding: 3px 7px; font-weight: 700; white-space: nowrap; }
  .miniMetrics { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 6px; margin-top: 10px; }
  .metric { min-width: 0; }
  .metric label { display: flex; justify-content: space-between; color: var(--muted); font-size: 10px; gap: 4px; }
  .bar { height: 5px; background: color-mix(in srgb, var(--muted) 20%, transparent); border-radius: 999px; overflow: hidden; margin-top: 3px; }
  .bar span { display: block; height: 100%; background: linear-gradient(90deg,var(--accent),var(--accent2)); border-radius: inherit; }
  .actions { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 10px; }
  button { border: 1px solid var(--vscode-button-border, transparent); color: var(--vscode-button-foreground); background: var(--vscode-button-background); border-radius: 6px; padding: 6px 9px; cursor: pointer; font: inherit; font-size: 12px; }
  button.secondary { background: transparent; color: var(--text); border-color: var(--border); }
  button:hover { background: var(--vscode-button-hoverBackground); }
  button.secondary:hover { background: var(--vscode-list-hoverBackground); }
  .composer { border-top: 1px solid var(--border); padding: 10px; display: grid; grid-template-columns: 1fr auto; gap: 8px; background: var(--panel); }
  textarea { resize: none; min-height: 42px; max-height: 140px; border-radius: 8px; border: 1px solid var(--border); background: var(--vscode-input-background); color: var(--text); padding: 9px; font: inherit; }
  .status { grid-column: 1 / -1; min-height: 18px; font-size: 12px; color: var(--muted); }
  .error { color: var(--vscode-errorForeground); }
  .empty { color: var(--muted); font-size: 12px; padding: 12px; border: 1px dashed var(--border); border-radius: 8px; }
</style>
</head>
<body>
  <div class="app">
    <header>
      <div class="brand"><span class="bolt">B</span><span>Bench</span></div>
      <div class="sub">Feature chat with generated options. Metrics are mocked until Docker is wired in.</div>
    </header>
    <main id="messages"></main>
    <form class="composer" id="form">
      <textarea id="input" placeholder="Ask Bench to build a feature..."></textarea>
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
      const optionId = target.closest('[data-option-id]')?.getAttribute('data-option-id');
      const action = target.getAttribute('data-action');
      if (!optionId || !action) return;
      vscode.postMessage({ type: action, optionId });
    });

    function render() {
      sendEl.disabled = Boolean(state.loading);
      statusEl.textContent = state.loading ? 'Generating options with Cerebras...' : (state.notice || '');
      statusEl.className = state.error ? 'status error' : 'status';
      messagesEl.innerHTML = '';

      for (const message of state.messages) {
        const node = document.createElement('section');
        node.className = 'msg ' + message.role;
        node.innerHTML = '<div class="role">' + escapeHtml(message.role) + '</div><div>' + escapeHtml(message.content) + '</div>';
        if (message.options?.length) {
          node.appendChild(renderCards(message.options));
        }
        messagesEl.appendChild(node);
      }

      if (state.messages.length === 0) {
        messagesEl.innerHTML = '<div class="empty">Start with a feature request.</div>';
      }

      messagesEl.scrollTop = messagesEl.scrollHeight;
    }

    function renderCards(options) {
      const wrap = document.createElement('div');
      wrap.className = 'cards';
      for (const option of options) {
        const card = document.createElement('article');
        card.className = 'card' + (option.selected ? ' selected' : '');
        card.setAttribute('data-option-id', option.id);
        card.innerHTML = \`
          <div class="cardTop">
            <div>
              <div class="title">\${escapeHtml(option.title)}</div>
              <div class="summary">\${escapeHtml(option.summary)}</div>
            </div>
            \${option.selected ? '<span class="selectedBadge">Selected</span>' : ''}
          </div>
          <div class="miniMetrics">
            \${metric('Read', option.metrics.readability)}
            \${metric('Speed', option.metrics.speed)}
            \${metric('Tests', option.metrics.testConfidence)}
          </div>
          <div class="actions">
            <button class="secondary" data-action="viewMetrics" type="button">View Metrics</button>
            <button class="secondary" data-action="viewCode" type="button">View Code</button>
            <button data-action="selectOption" type="button">\${option.selected ? 'Chosen' : 'Select'}</button>
          </div>\`;
        wrap.appendChild(card);
      }
      return wrap;
    }

    function metric(label, value) {
      return \`<div class="metric"><label><span>\${label}</span><span>\${value}</span></label><div class="bar"><span style="width:\${value}%"></span></div></div>\`;
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
    --card: var(--vscode-input-background);
    --border: var(--vscode-panel-border);
    --text: var(--vscode-foreground);
    --muted: var(--vscode-descriptionForeground);
    --accent: #8b6bff;
    --accent2: #19e3ff;
  }
  * { box-sizing: border-box; }
  body { margin: 0; padding: 16px; font-family: var(--vscode-font-family); color: var(--text); background: var(--panel); }
  h1 { font-size: 18px; line-height: 1.25; margin: 0; }
  .summary { color: var(--muted); margin-top: 6px; line-height: 1.45; }
  .tabs { display: flex; gap: 6px; margin: 16px 0; border-bottom: 1px solid var(--border); padding-bottom: 8px; }
  button { border: 1px solid var(--border); background: transparent; color: var(--text); border-radius: 6px; padding: 7px 10px; cursor: pointer; }
  button.active { color: #09090f; border-color: transparent; background: linear-gradient(120deg,var(--accent),var(--accent2)); font-weight: 700; }
  .panel { display: none; }
  .panel.active { display: block; }
  .metric { display: grid; grid-template-columns: 140px 1fr 44px; gap: 10px; align-items: center; margin: 12px 0; }
  .bar { height: 10px; background: color-mix(in srgb, var(--muted) 18%, transparent); border-radius: 999px; overflow: hidden; }
  .bar span { display: block; height: 100%; background: linear-gradient(90deg,var(--accent),var(--accent2)); }
  .card { border: 1px solid var(--border); border-radius: 8px; background: var(--card); padding: 12px; margin: 12px 0; }
  pre { white-space: pre-wrap; overflow: auto; border: 1px solid var(--border); border-radius: 8px; background: var(--vscode-textCodeBlock-background); padding: 12px; line-height: 1.45; }
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
    }).join('') + '<div class="card">Mock metric source: these values are placeholders for the future Docker sandbox runner.</div>';

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
  type: "ready" | "askFeature" | "viewMetrics" | "viewCode" | "selectOption" | "setApiKey";
  text?: string;
  optionId?: string;
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
      selected: false
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
      selected: false
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
      selected: false
    }
  ];
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
