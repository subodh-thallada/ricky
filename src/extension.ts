import * as vscode from "vscode";
import { CandidateUpdate, DaemonSandboxRunner, RunCallbacks } from "./services/daemonSandboxRunner";
import { DecisionPayload } from "./services/daemonClient";
import { OrchestratorClient } from "./services/orchestratorClient";
import { PreviewApplyProvider } from "./services/applyProvider";
import { BenchFixtureId, BenchOption, BenchRunState, ChatMessage, WorkspaceContext } from "./types";

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
  private readonly orchestrator = new OrchestratorClient();
  private readonly sandboxRunner = new DaemonSandboxRunner();
  private readonly applyProvider = new PreviewApplyProvider();
  private messages: ChatMessage[] = [];
  private options: BenchOption[] = [];
  private selectedOptionId?: string;
  private runState?: BenchRunState;
  private currentRunAbort?: AbortController;
  private lastPrompt = "";
  private decisionLog: string[] = [];

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
        case "selectOption":
          await this.selectOption(message.optionId);
          break;
        case "applySelected":
          await this.applySelected(message.optionId);
          break;
        case "rejectPreview":
          await this.rejectPreview(message.optionId);
          break;
        case "testAll":
          await this.testAll();
          break;
        case "applyWinner":
          await this.applyWinner();
          break;
        case "newFeatureChat":
          this.reset();
          break;
        case "viewLogs":
          await this.fetchCandidateLogs(message.optionId, message.openInEditor);
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
    this.runState = undefined;
    this.decisionLog = [];
    this.currentRunAbort?.abort();
    this.currentRunAbort = undefined;
    this.postState();
  }

  private async askFeature(text: string): Promise<void> {
    const prompt = text.trim();
    if (!prompt) {
      return;
    }

    this.messages.push({ id: createId("user"), role: "user", content: prompt });
    this.lastPrompt = prompt;
    this.runState = undefined;
    this.postState({ loading: true, notice: "Asking Gemini to plan, then Cerebras to write code..." });

    try {
      const workspaceContext = getWorkspaceContext();
      const result = await this.orchestrator.generateFeatureOptions(this.promptWithDecisionContext(prompt), workspaceContext);
      this.options = result.options;
      this.selectedOptionId = undefined;
      this.messages.push({
        id: createId("assistant"),
        role: "assistant",
        content: result.message || `I generated ${this.options.length} implementation options. Run them to populate measured metrics.`,
        options: this.options
      });
      this.postState();
    } catch (error) {
      this.options = buildFastApiAuthFallbackSuggestions(prompt);
      this.messages.push({
        id: createId("assistant"),
        role: "assistant",
        content: `I could not reach the Bench orchestrator, so I loaded local authenticated endpoint demo options that can still run through Docker. ${formatError(error)}`,
        options: this.options
      });
      this.postState({ error: formatError(error) });
    }
  }

  private async testAll(): Promise<void> {
    if (this.currentRunAbort) {
      return;
    }

    let fixtureId = this.pickFixtureId(this.options);
    if (this.options.length < 2 || !fixtureId) {
      this.options = buildFastApiAuthFallbackSuggestions(this.lastPrompt || "authenticated endpoint");
      fixtureId = "fastapi-auth-endpoint";
      this.messages.push({
        id: createId("system"),
        role: "system",
        content: "Loaded local FastAPI authenticated endpoint demo options so Docker evaluation has runnable candidates."
      });
    }

    const runnableOptions = this.validateOptionsForFixture(fixtureId, this.options);
    if (runnableOptions.length < 2) {
      this.options = buildFastApiAuthFallbackSuggestions(this.lastPrompt || "authenticated endpoint");
      fixtureId = "fastapi-auth-endpoint";
      this.messages.push({
        id: createId("system"),
        role: "system",
        content: "The generated cards were not compatible with a runnable endpoint fixture, so Bench swapped in FastAPI authenticated endpoint demo candidates."
      });
    }

    this.options = this.options.map((option) => ({
      ...option,
      runStatus: this.validateOptionsForFixture(fixtureId, [option]).length ? "queued" : option.runStatus,
      measured: undefined,
      candidateId: undefined,
      runId: undefined,
      logsUrl: undefined,
      codeUrl: undefined
    }));
    this.runState = {
      runId: "pending",
      fixtureId,
      status: "queued",
      summary: "Preparing Docker evaluation..."
    };
    this.syncMessageOptions();
    this.postState({ notice: "Starting local Docker evaluation..." });

    const abortController = new AbortController();
    this.currentRunAbort = abortController;

    try {
      const decision = await this.runFixture(
        fixtureId,
        this.options,
        {
          onRunCreated: (update) => {
            this.runState = {
              runId: update.runId,
              fixtureId,
              status: "running",
              summary: `Docker run ${update.runId} created. Waiting for candidate results...`
            };
            this.applyCandidateUpdates(update.options);
            this.postState({ notice: "Docker containers are running candidate tests..." });
          },
          onCandidateUpdate: (update) => {
            this.applyCandidateUpdates([update]);
            this.postState({ notice: `Candidate ${update.candidateId} is ${update.status}.` });
          },
          onDecision: (payload, updates) => {
            this.applyDecision(payload, updates);
            this.postState({ notice: payload.summary ?? "Docker evaluation completed." });
          }
        },
        abortController.signal
      );
      this.messages.push({
        id: createId("assistant"),
        role: "assistant",
        content: buildDecisionMessage(decision)
      });
      this.syncMessageOptions();
      this.postState();
    } catch (error) {
      this.runState = {
        runId: this.runState?.runId ?? "failed",
        fixtureId,
        status: "failed",
        summary: formatError(error)
      };
      this.options = this.options.map((option) => (
        option.runStatus === "queued" || option.runStatus === "running"
          ? { ...option, runStatus: "error" }
          : option
      ));
      this.syncMessageOptions();
      this.messages.push({
        id: createId("assistant"),
        role: "assistant",
        content: `Docker evaluation did not complete. ${formatError(error)}`,
        options: this.options
      });
      this.postState({ error: formatError(error) });
    } finally {
      if (this.currentRunAbort === abortController) {
        this.currentRunAbort = undefined;
      }
    }
  }

  private async applyWinner(): Promise<void> {
    const winnerCandidateId = this.runState?.winnerCandidateId;
    if (!winnerCandidateId) {
      return;
    }
    const winner = this.options.find((option) => option.candidateId === winnerCandidateId);
    if (!winner) {
      return;
    }
    if (winner.applyState === "previewed") {
      await this.applySelected(winner.id);
      return;
    }
    await this.selectOption(winner.id);
  }

  private async selectOption(optionId?: string): Promise<void> {
    const option = this.options.find((candidate) => candidate.id === optionId);
    if (!option) {
      return;
    }

    try {
      const preview = await this.applyProvider.preview(option.id, option, getWorkspaceContext());
      const deactivated = new Set(preview.deactivatedSessionIds ?? []);
      this.options = this.options.map((candidate) => {
        if (candidate.id === option.id) {
          return {
            ...candidate,
            selected: true,
            applyState: "previewed",
            applySummary: preview.summary
          };
        }
        if (deactivated.has(candidate.id) || candidate.applyState === "previewed") {
          return {
            ...candidate,
            selected: false,
            applyState: "idle",
            applySummary: undefined
          };
        }
        return { ...candidate, selected: false };
      });
      this.selectedOptionId = option.id;
      this.recordDecision(`User previewed "${option.title}". Summary: ${option.summary}`);
      this.syncMessageOptions();
      void vscode.window.showInformationMessage(`Preview loaded for "${option.title}". Review it in the editor before applying.`);
      this.postState({ notice: preview.summary });
    } catch (error) {
      const message = `Bench could not preview "${option.title}". ${formatError(error)}`;
      void vscode.window.showErrorMessage(message);
      this.postState({ error: message });
    }
  }

  private async applySelected(optionId?: string): Promise<void> {
    const id = optionId ?? this.selectedOptionId;
    const option = this.options.find((candidate) => candidate.id === id);
    if (!option) {
      return;
    }

    try {
      const result = await this.applyProvider.applySelected(option.id);
      if (!result) {
        await this.selectOption(option.id);
        return;
      }

      this.options = this.options.map((candidate) => (
        candidate.id === option.id
          ? {
              ...candidate,
              selected: true,
              applyState: "applied",
              applySummary: result.summary
            }
          : {
              ...candidate,
              selected: false,
              applyState: candidate.applyState === "previewed" ? "idle" : candidate.applyState,
              applySummary: candidate.applyState === "previewed" ? undefined : candidate.applySummary
            }
      ));
      this.selectedOptionId = option.id;
      this.recordDecision(`User applied "${option.title}". ${result.summary}`);
      this.syncMessageOptions();
      void vscode.window.showInformationMessage(result.summary);
      this.postState({ notice: result.summary });
    } catch (error) {
      const message = formatError(error);
      void vscode.window.showErrorMessage(message);
      this.postState({ error: message });
    }
  }

  private async rejectPreview(optionId?: string): Promise<void> {
    const id = optionId ?? this.selectedOptionId;
    const option = this.options.find((candidate) => candidate.id === id);
    if (!option) {
      return;
    }

    const summary = await this.applyProvider.rejectSelected(option.id);
    this.options = this.options.map((candidate) => (
      candidate.id === option.id
        ? {
            ...candidate,
            selected: false,
            applyState: "idle",
            applySummary: undefined
          }
        : candidate
    ));
    if (this.selectedOptionId === option.id) {
      this.selectedOptionId = undefined;
    }
    this.syncMessageOptions();
    this.postState({ notice: summary ?? `Preview closed for ${option.title}.` });
  }

  private postState(extra: Partial<WebviewState> = {}): void {
    this.view?.webview.postMessage({
      type: "state",
      state: {
        messages: this.messages,
        options: this.options,
        selectedOptionId: this.selectedOptionId,
        runState: this.runState,
        loading: false,
        ...extra
      } satisfies WebviewState
    });
  }

  private applyCandidateUpdates(updates: CandidateUpdate[]): void {
    const byOptionId = new Map(updates.map((update) => [update.optionId, update]));
    this.options = this.options.map((option) => {
      const update = byOptionId.get(option.id);
      if (!update) {
        return option;
      }
      return {
        ...option,
        candidateId: update.candidateId,
        runId: update.runId ?? option.runId,
        runStatus: update.status,
        measured: update.measured ?? option.measured,
        logsUrl: update.logsUrl ?? option.logsUrl,
        codeUrl: update.codeUrl ?? option.codeUrl
      };
    });
    this.syncMessageOptions();
  }

  private applyDecision(payload: DecisionPayload, updates: CandidateUpdate[]): void {
    this.applyCandidateUpdates(updates);
    this.runState = {
      runId: payload.run_id,
      fixtureId: payload.fixture_id as BenchFixtureId,
      status: payload.status,
      winnerCandidateId: payload.winner_candidate_id,
      summary: payload.summary
    };
    this.recordDecision(buildDecisionLogEntry(payload));
  }

  private recordDecision(entry: string): void {
    this.decisionLog.push(entry);
    this.decisionLog = this.decisionLog.slice(-8);
  }

  private promptWithDecisionContext(prompt: string): string {
    if (!this.decisionLog.length) {
      return prompt;
    }
    return [
      prompt,
      "",
      "Existing Bench planning context from this chat:",
      ...this.decisionLog.map((entry, index) => `${index + 1}. ${entry}`),
      "",
      "Use this context when generating the next options. Preserve decisions the user already made unless the new prompt explicitly changes them."
    ].join("\n");
  }

  private pickFixtureId(options: BenchOption[]): BenchFixtureId | undefined {
    if (this.sandboxRunner.validateFastApiAuthOptions(options).length >= 2) {
      return "fastapi-auth-endpoint";
    }
    if (this.sandboxRunner.validatePythonMergeOptions(options).length >= 2) {
      return "python-merge";
    }
    return undefined;
  }

  private validateOptionsForFixture(fixtureId: BenchFixtureId, options: BenchOption[]): BenchOption[] {
    return fixtureId === "fastapi-auth-endpoint"
      ? this.sandboxRunner.validateFastApiAuthOptions(options)
      : this.sandboxRunner.validatePythonMergeOptions(options);
  }

  private async runFixture(
    fixtureId: BenchFixtureId,
    options: BenchOption[],
    callbacks: RunCallbacks,
    signal?: AbortSignal
  ): Promise<DecisionPayload> {
    return fixtureId === "fastapi-auth-endpoint"
      ? this.sandboxRunner.runFastApiAuthEndpoint(options, callbacks, signal)
      : this.sandboxRunner.runPythonMerge(options, callbacks, signal);
  }

  private async fetchCandidateLogs(optionId?: string, openInEditor?: boolean): Promise<void> {
    const option = this.options.find((candidate) => candidate.id === optionId);
    if (!option) {
      return;
    }

    if (!option.logsUrl) {
      if (openInEditor) {
        void vscode.window.showInformationMessage("No Docker logs yet. Run Test all first.");
        return;
      }
      this.view?.webview.postMessage({
        type: "logs",
        optionId: option.id,
        status: option.runStatus,
        notice: "No Docker logs yet — run Test all first."
      });
      return;
    }

    try {
      const content = await this.sandboxRunner.fetchText(option.logsUrl);
      if (openInEditor) {
        const doc = await vscode.workspace.openTextDocument({
          content,
          language: "log"
        });
        await vscode.window.showTextDocument(doc, { preview: false });
        return;
      }
      this.view?.webview.postMessage({
        type: "logs",
        optionId: option.id,
        status: option.runStatus,
        content
      });
    } catch (error) {
      const message = formatError(error);
      if (openInEditor) {
        void vscode.window.showErrorMessage(`Bench could not load Docker logs. ${message}`);
        return;
      }
      this.view?.webview.postMessage({
        type: "logs",
        optionId: option.id,
        status: option.runStatus,
        error: message
      });
    }
  }

  private syncMessageOptions(): void {
    let replaced = false;
    this.messages = [...this.messages].reverse().map((message) => {
      if (!replaced && message.options) {
        replaced = true;
        return { ...message, options: this.options };
      }
      return message;
    }).reverse();
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
    --bg: #0e0e0e;
    --surface: #131313;
    --surface-raised: #1c1b1b;
    --surface-high: #201f1f;
    --surface-track: #1a1a1a;
    --border: #1f1f1f;
    --border-strong: #333333;
    --text: #e5e2e1;
    --text-soft: #c2c6d7;
    --mute: #8c90a0;
    --dim: #5f6573;
    --primary: #7aa2ff;
    --primary-strong: #3e7bfa;
    --pass: #63d471;
    --fail: #ff6f61;
    --run: #b1c5ff;
    --warn: #ffb68c;
    --btn-fill: #b1c5ff;
    --btn-text: #071633;
    --mono: 'JetBrains Mono', var(--vscode-editor-font-family), monospace;
    --sans: 'Geist', var(--vscode-font-family), system-ui, sans-serif;
  }
  * { box-sizing: border-box; }
  html {
    height: 100%;
    overflow: hidden;
  }
  button, input { font: inherit; }
  body {
    background: var(--bg);
    color: var(--text);
    font-family: var(--sans);
    font-size: 13px;
    line-height: 18px;
    margin: 0;
    padding: 0;
    height: 100vh;
    height: 100dvh;
    min-height: 0;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }
  .header {
    display: flex;
    justify-content: space-between;
    gap: 12px;
    padding: 12px;
    border-bottom: 1px solid var(--border);
    background: rgba(19, 19, 19, 0.94);
    align-items: center;
    flex-shrink: 0;
  }
  .brand { display: grid; gap: 2px; min-width: 0; }
  .brand-row { display: flex; align-items: center; gap: 8px; min-width: 0; }
  .brand-mark {
    width: 20px;
    height: 20px;
    display: grid;
    place-items: center;
    border: 1px solid var(--border-strong);
    border-radius: 5px;
    background: var(--surface-high);
    color: var(--primary);
    font-family: var(--mono);
    font-size: 11px;
    font-weight: 700;
  }
  .title {
    font-size: 14px;
    font-weight: 650;
    line-height: 20px;
    letter-spacing: -0.01em;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .sub {
    color: var(--mute);
    font-family: var(--mono);
    font-size: 10px;
    letter-spacing: 0.05em;
    line-height: 12px;
    text-transform: uppercase;
  }
  .icons { display: flex; gap: 6px; }
  .icon-btn {
    width: 28px;
    height: 28px;
    display: grid;
    place-items: center;
    border: 1px solid transparent;
    border-radius: 6px;
    color: var(--text-soft);
    cursor: pointer;
    transition: background 160ms ease, border-color 160ms ease, color 160ms ease;
  }
  .icon-btn:hover {
    background: var(--surface-high);
    border-color: var(--border-strong);
    color: var(--text);
  }
  .chat {
    padding: 12px;
    display: flex;
    flex-direction: column;
    gap: 12px;
    overflow-y: auto;
    flex: 1;
    min-height: 0;
  }
  .chat::-webkit-scrollbar { width: 8px; }
  .chat::-webkit-scrollbar-thumb { background: var(--border-strong); border-radius: 999px; border: 2px solid var(--bg); }
  .msg {
    border: 1px solid var(--border);
    border-radius: 4px;
    background: rgba(19, 19, 19, 0.72);
    color: var(--text-soft);
    min-width: 0;
    overflow-wrap: anywhere;
  }
  .msg.user {
    padding: 12px;
    background: transparent;
    color: var(--text);
  }
  .msg.assistant {
    display: flex;
    gap: 10px;
    flex-direction: column;
    padding: 10px;
  }
  .msg.system {
    padding: 10px;
    color: var(--mute);
    font-family: var(--mono);
    font-size: 11px;
    line-height: 16px;
  }
  .msg-content {
    display: grid;
    grid-template-columns: 20px minmax(0, 1fr);
    gap: 8px;
    align-items: flex-start;
    min-width: 0;
  }
  .msg-content > div { min-width: 0; overflow-wrap: anywhere; }
  .agent-dot {
    width: 20px;
    height: 20px;
    border: 1px solid var(--border-strong);
    border-radius: 4px;
    background: var(--surface-high);
    display: grid;
    place-items: center;
  }
  .agent-dot i { width: 13px; height: 13px; }
  .cards { display: flex; flex-direction: column; gap: 8px; width: 100%; }
  .card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 4px;
    position: relative;
    overflow: hidden;
    transition: background 160ms ease, border-color 160ms ease;
  }
  .card::before {
    content: "";
    position: absolute;
    inset: 0 auto 0 0;
    width: 3px;
    background: var(--dim);
  }
  .card:hover { border-color: var(--border-strong); background: var(--surface-raised); }
  .card.selected { border-color: rgba(177, 197, 255, 0.64); box-shadow: inset 0 0 0 1px rgba(177, 197, 255, 0.14); }
  .card.passed::before { background: var(--pass); }
  .card.failed::before, .card.error::before, .card.timeout::before { background: var(--fail); }
  .card.running::before, .card.queued::before { background: var(--run); }
  .card.selected::before { background: var(--primary); }
  .card-top {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    gap: 8px;
    align-items: start;
    padding: 10px 10px 8px 12px;
  }
  .card-title-wrap { display: grid; gap: 6px; min-width: 0; }
  .card-meta { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; min-width: 0; }
  .badge, .status-pill {
    font-family: var(--mono);
    font-size: 10px;
    font-weight: 600;
    line-height: 14px;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    white-space: nowrap;
  }
  .badge {
    color: var(--primary);
    background: rgba(177, 197, 255, 0.08);
    border: 1px solid rgba(177, 197, 255, 0.18);
    border-radius: 999px;
    padding: 1px 7px;
  }
  .status-pill { color: var(--mute); }
  .passed .status-pill { color: var(--pass); }
  .failed .status-pill, .error .status-pill, .timeout .status-pill { color: var(--fail); }
  .running .status-pill, .queued .status-pill { color: var(--run); }
  .card-title {
    color: var(--text);
    font-size: 14px;
    font-weight: 650;
    line-height: 19px;
    letter-spacing: -0.01em;
    overflow-wrap: anywhere;
  }
  .card-summary {
    color: var(--mute);
    font-size: 12px;
    line-height: 16px;
    padding: 0 10px 10px 12px;
  }
  .status-icon i { width: 16px; height: 16px; }
  .running .status-icon i, .queued .status-icon i { animation: spin 2s linear infinite; }
  @keyframes spin { 100% { transform: rotate(360deg); } }
  .metrics {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 7px;
    padding: 0 10px 10px 12px;
  }
  .metric-col { display: flex; flex-direction: column; gap: 5px; min-width: 0; }
  .metric-title, .metric-val {
    font-family: var(--mono);
    font-size: 10px;
    line-height: 14px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .metric-title { color: var(--mute); text-transform: uppercase; letter-spacing: 0.04em; }
  .metric-bar-bg { height: 4px; background: var(--surface-track); border-radius: 999px; width: 100%; overflow: hidden; }
  .metric-bar-fg { height: 100%; border-radius: inherit; width: 100%; background: var(--dim); }
  .passed .metric-bar-fg { background: var(--pass); }
  .failed .metric-bar-fg, .error .metric-bar-fg, .timeout .metric-bar-fg { background: var(--fail); }
  .running .metric-bar-fg, .queued .metric-bar-fg { background: var(--run); }
  .draft .metric-bar-fg { background: var(--mute); }
  .metric-val { color: var(--text-soft); }
  .failed .metric-val, .error .metric-val, .timeout .metric-val { color: var(--fail); }
  .running .metric-val, .queued .metric-val { color: var(--mute); }
  .card-actions {
    display: flex;
    justify-content: space-between;
    gap: 8px;
    align-items: center;
    padding: 8px 10px 9px 12px;
    border-top: 1px solid var(--border);
    background: rgba(0, 0, 0, 0.16);
  }
  .left-actions { display: flex; gap: 10px; min-width: 0; }
  .action-btn {
    display: flex;
    align-items: center;
    gap: 5px;
    background: transparent;
    border: none;
    color: var(--mute);
    font-family: var(--mono);
    font-size: 10px;
    line-height: 14px;
    cursor: pointer;
    padding: 2px 0;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    transition: color 160ms ease;
  }
  .action-btn:hover { color: var(--text); }
  .failed .action-btn.view-logs, .error .action-btn.view-logs, .timeout .action-btn.view-logs { color: var(--fail); }
  .select-btn {
    background: var(--btn-fill);
    color: var(--btn-text);
    border: 1px solid rgba(255, 255, 255, 0.12);
    padding: 5px 10px;
    border-radius: 6px;
    font-weight: 700;
    font-size: 11px;
    line-height: 16px;
    cursor: pointer;
    white-space: nowrap;
    transition: background 160ms ease, border-color 160ms ease, color 160ms ease, filter 160ms ease;
  }
  .selected .select-btn { background: transparent; color: var(--run); border-color: rgba(177, 197, 255, 0.42); }
  .select-btn:hover { filter: brightness(1.08); }
  .select-btn:disabled { opacity: 0.5; cursor: not-allowed; }
  button:disabled { cursor: not-allowed; opacity: 0.52; }
  .composer-wrap {
    flex-shrink: 0;
    padding: 10px 12px 12px;
    background: var(--bg);
    border-top: 1px solid var(--border);
  }
  .run-bar {
    display: none;
    justify-content: space-between;
    align-items: center;
    gap: 8px;
    margin-bottom: 8px;
    padding: 7px 8px;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 4px;
  }
  .run-bar.visible { display: flex; }
  .run-status {
    font-family: var(--mono);
    font-size: 10px;
    line-height: 14px;
    color: var(--mute);
    display: flex;
    align-items: center;
    gap: 6px;
    min-width: 0;
    flex: 1;
    overflow-wrap: anywhere;
  }
  .run-actions { display: flex; gap: 8px; }
  .outline-btn {
    background: transparent;
    border: 1px solid var(--border-strong);
    color: var(--text-soft);
    padding: 4px 8px;
    font-size: 10px;
    line-height: 14px;
    border-radius: 6px;
    font-family: var(--mono);
    cursor: pointer;
    white-space: nowrap;
    transition: border-color 160ms ease, color 160ms ease, background 160ms ease;
  }
  .outline-btn:hover:not(:disabled) { border-color: var(--text); color: var(--text); }
  .outline-btn:disabled { opacity: 0.5; cursor: not-allowed; }
  .input-box {
    display: flex;
    align-items: center;
    gap: 9px;
    background: var(--surface);
    border: 1px solid var(--border-strong);
    border-radius: 4px;
    padding: 8px 8px 8px 10px;
  }
  .input-box:focus-within { border-color: var(--primary); }
  .input-box i { flex-shrink: 0; cursor: pointer; }
  .input-box input { flex: 1; min-width: 0; background: transparent; border: none; color: var(--text); font-family: var(--sans); font-size: 13px; outline: none; }
  .input-box input::placeholder { color: var(--mute); }
  .send-btn { width: 26px; height: 26px; border: 1px solid transparent; border-radius: 4px; display: grid; place-items: center; background: transparent; padding: 0; cursor: pointer; transition: background 160ms ease, border-color 160ms ease; }
  .send-btn:hover { background: var(--surface-high); border-color: var(--border-strong); }
  .composer-status {
    min-height: 14px;
    margin-bottom: 8px;
    color: var(--mute);
    font-family: var(--mono);
    font-size: 10px;
    line-height: 14px;
    letter-spacing: 0.02em;
  }
  .composer-status.error { color: var(--fail); }
  .inline-details {
    display: grid;
    gap: 8px;
    padding: 10px 10px 10px 12px;
    border-top: 1px solid var(--border);
    background: #0f0f0f;
  }
  .detail-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 6px; }
  .detail-stat {
    min-width: 0;
    padding: 7px;
    border: 1px solid var(--border);
    border-radius: 4px;
    background: var(--surface);
  }
  .detail-label {
    color: var(--mute);
    font-family: var(--mono);
    font-size: 10px;
    line-height: 14px;
    letter-spacing: 0.04em;
    text-transform: uppercase;
  }
  .detail-value {
    margin-top: 2px;
    color: var(--text);
    font-family: var(--mono);
    font-size: 11px;
    line-height: 14px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .detail-section {
    display: grid;
    gap: 4px;
    color: var(--text-soft);
    font-size: 12px;
    line-height: 16px;
    min-width: 0;
    overflow-wrap: anywhere;
  }
  .detail-section-title {
    color: var(--mute);
    font-family: var(--mono);
    font-size: 10px;
    font-weight: 600;
    line-height: 14px;
    letter-spacing: 0.05em;
    text-transform: uppercase;
  }
  .detail-list {
    display: grid;
    gap: 4px;
    margin: 0;
    padding: 0;
    list-style: none;
  }
  .detail-list li {
    padding-left: 10px;
    position: relative;
    min-width: 0;
    overflow-wrap: anywhere;
  }
  .detail-list li::before {
    content: "";
    position: absolute;
    left: 0;
    top: 7px;
    width: 4px;
    height: 4px;
    border-radius: 999px;
    background: var(--outline-dot, var(--dim));
  }
  .issue-list li::before { background: var(--fail); }
  .empty-detail { color: var(--mute); font-family: var(--mono); font-size: 10px; line-height: 14px; }
  button:focus-visible, input:focus-visible, .icon-btn:focus-visible {
    outline: 1px solid var(--primary);
    outline-offset: 2px;
  }
  @media (prefers-reduced-motion: reduce) {
    *, *::before, *::after {
      animation-duration: 0.01ms !important;
      animation-iteration-count: 1 !important;
      transition-duration: 0.01ms !important;
    }
  }
  @media (max-width: 300px) {
    .detail-grid { grid-template-columns: 1fr; }
  }
  .svg-sparkle { background: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='%23b1c5ff' d='M11.5 2L13 8l6 1.5-6 1.5-1.5 6-1.5-6-6-1.5 6-1.5z'/%3E%3C/svg%3E") no-repeat center / contain; display: inline-block; }
  .svg-check { background: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='%2363d471' d='M9.6 16.2 5.8 12.4l1.4-1.4 2.4 2.4 7.2-7.2 1.4 1.4z'/%3E%3C/svg%3E") no-repeat center / contain; display: inline-block; width: 16px; height: 16px; }
  .svg-spin { background: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='%23b1c5ff' d='M12 4V2C6.48 2 2 6.48 2 12h2c0-4.41 3.59-8 8-8z'/%3E%3C/svg%3E") no-repeat center / contain; display: inline-block; width: 16px; height: 16px; }
  .svg-alert { background: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='%23ff6f61' d='M11 6h2v8h-2zm0 10h2v2h-2z'/%3E%3C/svg%3E") no-repeat center / contain; display: inline-block; width: 16px; height: 16px; }
  .svg-draft { background: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Ccircle cx='12' cy='12' r='6' fill='none' stroke='%238c90a0' stroke-width='2'/%3E%3C/svg%3E") no-repeat center / contain; display: inline-block; width: 16px; height: 16px; }
  .svg-metrics { background: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='%238c90a0' d='M5 9.2h3V19H5zM10.6 5h2.8v14h-2.8zm5.6 8H19v6h-2.8z'/%3E%3C/svg%3E") no-repeat center / contain; display: inline-block; width: 14px; height: 14px; }
  .svg-logs { background: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='%238c90a0' d='M20 19H4v-1h16v1zm0-14H4v1h16V5zm0 7H4v1h16v-1z'/%3E%3C/svg%3E") no-repeat center / contain; display: inline-block; width: 14px; height: 14px; }
  .svg-send { background: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='%23b1c5ff' d='M2.01 21 23 12 2.01 3 2 10l15 2-15 2z'/%3E%3C/svg%3E") no-repeat center / contain; width: 15px; height: 15px; display: inline-block; }
  .svg-plus { background: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='%23c2c6d7' d='M19 13h-6v6h-2v-6H5v-2h6V5h2v6h6z'/%3E%3C/svg%3E") no-repeat center / contain; width: 16px; height: 16px; display: inline-block; }
  .svg-close { background: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='%23c2c6d7' d='M18.3 5.71 12 12l6.3 6.29-1.41 1.42L10.59 13.4 4.29 19.7 2.88 18.3 9.17 12 2.88 5.71 4.29 4.3l6.3 6.29 6.29-6.3z'/%3E%3C/svg%3E") no-repeat center / contain; width: 16px; height: 16px; display: inline-block; }
  .svg-open { background: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='%23c2c6d7' d='M19 19H5V5h7V3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7h-2v7zM14 3v2h3.59l-9.83 9.83 1.41 1.41L19 6.41V10h2V3h-7z'/%3E%3C/svg%3E") no-repeat center / contain; width: 16px; height: 16px; display: inline-block; }
  .svg-terminal { background: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath fill='%23b1c5ff' d='M4 6h16v12H4V6zm2 2v8h12V8H6zm2 2 3 2-3 2v-1.5l1.5-1-1.5-1V10zm4 0h4v1.5h-4V10z'/%3E%3C/svg%3E") no-repeat center / contain; width: 16px; height: 16px; display: inline-block; }
  .logs-overlay {
    position: fixed;
    inset: 0;
    z-index: 100;
    display: none;
    flex-direction: column;
    background: rgba(0, 0, 0, 0.72);
    backdrop-filter: blur(2px);
  }
  .logs-overlay.open { display: flex; }
  .logs-panel {
    display: flex;
    flex-direction: column;
    flex: 1;
    min-height: 0;
    margin-left: auto;
    width: 100%;
    max-width: 100%;
    background: var(--surface);
    border-left: 1px solid var(--border-strong);
    box-shadow: -8px 0 24px rgba(0, 0, 0, 0.35);
  }
  .logs-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    padding: 10px 12px;
    border-bottom: 1px solid var(--border);
    background: var(--surface-high);
    flex-shrink: 0;
  }
  .logs-header-left {
    display: flex;
    align-items: center;
    gap: 8px;
    min-width: 0;
    flex: 1;
  }
  .logs-title {
    font-size: 14px;
    font-weight: 650;
    line-height: 20px;
    letter-spacing: -0.01em;
    color: var(--text);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .logs-header-actions { display: flex; gap: 4px; flex-shrink: 0; }
  .logs-tabs {
    display: flex;
    gap: 0;
    padding: 0 12px;
    border-bottom: 1px solid var(--border);
    background: var(--surface);
    flex-shrink: 0;
  }
  .logs-tab {
    background: transparent;
    border: none;
    border-bottom: 2px solid transparent;
    color: var(--mute);
    cursor: pointer;
    font-family: var(--mono);
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 0.05em;
    line-height: 14px;
    margin-bottom: -1px;
    padding: 10px 12px;
    text-transform: uppercase;
    transition: color 160ms ease, border-color 160ms ease;
  }
  .logs-tab:hover { color: var(--text-soft); }
  .logs-tab.active {
    color: var(--primary);
    border-bottom-color: var(--primary);
  }
  .logs-body {
    flex: 1;
    min-height: 0;
    overflow-y: auto;
    background: #0a0a0a;
  }
  .logs-body::-webkit-scrollbar { width: 8px; }
  .logs-body::-webkit-scrollbar-thumb { background: var(--border-strong); border-radius: 999px; border: 2px solid #0a0a0a; }
  .logs-pane { display: none; min-height: 100%; }
  .logs-pane.active { display: block; }
  .logs-terminal {
    padding: 10px 12px;
    font-family: var(--mono);
    font-size: 11px;
    line-height: 16px;
    color: #d4d4d4;
    white-space: pre-wrap;
    word-break: break-word;
  }
  .log-line { display: block; }
  .log-line.system { color: var(--mute); }
  .log-line.error { color: var(--fail); }
  .log-line.success { color: var(--pass); }
  .empty-state {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    height: 100%;
    text-align: center;
  }
  .logs-empty {
    padding: 16px 12px;
    color: var(--mute);
    font-family: var(--mono);
    font-size: 11px;
    line-height: 16px;
  }
  .logs-pane-details { padding: 12px; background: #0f0f0f; }
  .logs-pane-metrics { padding: 12px; background: #0f0f0f; }
  .logs-pane-metrics .metrics { padding: 0; }
  .logs-footer {
    display: flex;
    justify-content: flex-end;
    gap: 8px;
    padding: 10px 12px;
    border-top: 1px solid var(--border);
    background: var(--surface-high);
    flex-shrink: 0;
  }
  .logs-footer .outline-btn {
    font-size: 11px;
    line-height: 16px;
    padding: 5px 10px;
  }
  .logs-footer .select-btn {
    font-size: 11px;
    line-height: 16px;
    padding: 5px 12px;
  }
</style>
</head>
<body>
  <div class="header">
    <div class="brand">
      <div class="brand-row"><span class="title">Bench</span></div>
      <div class="sub">AI developer sidepanel</div>
    </div>
    <div class="icons">
      <button class="icon-btn" type="button" title="New Chat" aria-label="Start new chat" data-action="newChat"><i class="svg-plus"></i></button>
    </div>
  </div>

  <div class="chat" id="messages"></div>

  <div class="composer-wrap">
    <div class="run-bar" id="runBar"></div>
    <div class="composer-status" id="status" aria-live="polite"></div>
    <form id="form" class="input-box">
      <span class="svg-plus" aria-hidden="true"></span>
      <input type="text" id="input" placeholder="Ask Bench to build or refine..." autocomplete="off" aria-label="Feature request">
      <button class="send-btn" type="submit" title="Send"><i class="svg-send"></i></button>
    </form>
  </div>

  <div class="logs-overlay" id="logsOverlay" aria-hidden="true">
    <div class="logs-panel" role="dialog" aria-modal="true" aria-labelledby="logsTitle">
      <div class="logs-header">
        <div class="logs-header-left">
          <i class="svg-terminal" aria-hidden="true"></i>
          <span class="logs-title" id="logsTitle">Approach A - Logs</span>
        </div>
        <div class="logs-header-actions">
          <button class="icon-btn" type="button" title="Open in editor" aria-label="Open logs in editor" data-action="openLogsInEditor"><i class="svg-open"></i></button>
          <button class="icon-btn" type="button" title="Close" aria-label="Close logs panel" data-action="closeLogs"><i class="svg-close"></i></button>
        </div>
      </div>
      <div class="logs-tabs" role="tablist">
        <button class="logs-tab" type="button" role="tab" data-tab="details" aria-selected="false">Details</button>
        <button class="logs-tab active" type="button" role="tab" data-tab="logs" aria-selected="true">Logs</button>
        <button class="logs-tab" type="button" role="tab" data-tab="metrics" aria-selected="false">Metrics</button>
      </div>
      <div class="logs-body">
        <div class="logs-pane logs-pane-details" data-pane="details"></div>
        <div class="logs-pane logs-pane-logs active" data-pane="logs">
          <div class="logs-empty" id="logsContent">Loading Docker logs...</div>
        </div>
        <div class="logs-pane logs-pane-metrics" data-pane="metrics"></div>
      </div>
      <div class="logs-footer">
        <button class="outline-btn" type="button" data-action="clearLogs">Clear</button>
        <button class="select-btn" type="button" data-action="copyLogs">Copy Logs</button>
      </div>
    </div>
  </div>

  <script nonce="${nonce}">
    const vscode = acquireVsCodeApi();
    const messagesEl = document.getElementById('messages');
    const formEl = document.getElementById('form');
    const inputEl = document.getElementById('input');
    const runBarEl = document.getElementById('runBar');
    const statusEl = document.getElementById('status');
    const sendEl = document.querySelector('.send-btn');
    const logsOverlayEl = document.getElementById('logsOverlay');
    const logsTitleEl = document.getElementById('logsTitle');

    let state = normalizeState({ messages: [], options: [], loading: false });
    let logsPanel = {
      optionId: undefined,
      optionIndex: 0,
      activeTab: 'logs',
      logText: '',
      loading: false
    };

    window.addEventListener('message', (event) => {
      const data = event.data;
      if (data && data.type === 'state') {
        state = normalizeState(data.state);
        render();
        if (logsPanel.optionId) {
          refreshLogsPanelContent();
        }
      } else if (data && data.type === 'logs') {
        handleLogsMessage(data);
      }
    });

    formEl.addEventListener('submit', (event) => {
      event.preventDefault();
      const text = inputEl.value.trim();
      if (!text || state.loading) return;
      inputEl.value = '';
      state = normalizeState({
        ...state,
        loading: true,
        notice: 'Planning approaches...',
        error: undefined,
        messages: [
          ...(state.messages || []),
          { id: 'local-user-' + Date.now(), role: 'user', content: text }
        ]
      });
      render();
      vscode.postMessage({ type: 'askFeature', text });
    });

    document.addEventListener('click', (event) => {
      const target = event.target instanceof Element ? event.target : event.target?.parentElement;
      if (!target) return;

      let actionNode = target.closest('[data-action]');
      if (!actionNode) actionNode = target;

      const action = actionNode.getAttribute('data-action');
      const optionId = target.closest('[data-option-id]')?.getAttribute('data-option-id') || logsPanel.optionId;
      const tab = actionNode.getAttribute('data-tab');

      if (action === 'newChat') {
        closeLogsPanel();
        vscode.postMessage({ type: 'newFeatureChat' });
      }
      else if (action === 'viewLogs' && optionId) {
        openLogsPanel(optionId);
      }
      else if (action === 'closeLogs') {
        closeLogsPanel();
      }
      else if (action === 'openLogsInEditor' && logsPanel.optionId) {
        vscode.postMessage({ type: 'viewLogs', optionId: logsPanel.optionId, openInEditor: true });
      }
      else if (action === 'clearLogs') {
        clearLogsContent();
      }
      else if (action === 'copyLogs') {
        copyLogsContent();
      }
      else if (tab) {
        setLogsTab(tab);
      }
      else if (action && optionId) vscode.postMessage({ type: action, optionId });
      else if (action) vscode.postMessage({ type: action });
    });

    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && logsOverlayEl.classList.contains('open')) {
        closeLogsPanel();
      }
    });

    logsOverlayEl.addEventListener('click', (event) => {
      if (event.target === logsOverlayEl) {
        closeLogsPanel();
      }
    });

    function normalizeState(next) {
      const source = isRecord(next) ? next : {};
      const messages = Array.isArray(source.messages)
        ? source.messages.map(normalizeMessage).filter(Boolean)
        : [];
      const options = Array.isArray(source.options)
        ? source.options.map(normalizeOption).filter(Boolean)
        : [];

      return {
        messages,
        options,
        selectedOptionId: textOrUndefined(source.selectedOptionId),
        runState: isRecord(source.runState) ? source.runState : undefined,
        loading: Boolean(source.loading),
        notice: textOrUndefined(source.notice),
        error: textOrUndefined(source.error)
      };
    }

    function normalizeMessage(raw, index) {
      const source = isRecord(raw) ? raw : {};
      const role = source.role === 'user' || source.role === 'system' || source.role === 'assistant'
        ? source.role
        : 'assistant';
      const message = {
        id: textOrUndefined(source.id) || 'message-' + index,
        role,
        content: textOrEmpty(source.content)
      };
      if (Array.isArray(source.options)) {
        message.options = source.options.map(normalizeOption).filter(Boolean);
      }
      return message;
    }

    function normalizeOption(raw, index) {
      const source = isRecord(raw) ? raw : {};
      return {
        id: textOrUndefined(source.id) || 'option-' + index,
        title: textOrUndefined(source.title) || 'Untitled option',
        summary: textOrEmpty(source.summary),
        implementationPlan: textOrUndefined(source.implementationPlan) || textOrUndefined(source.implementation_plan) || '',
        tradeoffs: Array.isArray(source.tradeoffs) ? source.tradeoffs.map(textOrEmpty) : [],
        generatedCode: textOrUndefined(source.generatedCode) || textOrUndefined(source.generated_code) || '',
        metrics: isRecord(source.metrics) ? source.metrics : undefined,
        candidateId: textOrUndefined(source.candidateId),
        runId: textOrUndefined(source.runId),
        runStatus: normalizeRunStatus(source.runStatus),
        measured: isRecord(source.measured) ? source.measured : undefined,
        logsUrl: textOrUndefined(source.logsUrl),
        codeUrl: textOrUndefined(source.codeUrl),
        selected: Boolean(source.selected),
        applyState: normalizeApplyState(source.applyState),
        applySummary: textOrUndefined(source.applySummary)
      };
    }

    function normalizeApplyState(value) {
      const state = textOrUndefined(value);
      return ['idle', 'previewed', 'applied'].includes(state) ? state : 'idle';
    }

    function normalizeRunStatus(value) {
      const status = textOrUndefined(value);
      return ['draft', 'queued', 'running', 'passed', 'failed', 'timeout', 'error'].includes(status) ? status : 'draft';
    }

    function normalizeTests(value) {
      if (!isRecord(value)) return undefined;
      const passed = Number(value.passed);
      const failed = Number(value.failed);
      const total = Number(value.total);
      if (!Number.isFinite(passed) || !Number.isFinite(failed) || !Number.isFinite(total)) return undefined;
      return { passed, failed, total };
    }

    function isRecord(value) {
      return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
    }

    function textOrUndefined(value) {
      return value === undefined || value === null ? undefined : String(value);
    }

    function textOrEmpty(value) {
      return value === undefined || value === null ? '' : String(value);
    }

    function percent(value) {
      const numberValue = Number(value);
      if (!Number.isFinite(numberValue)) return '0%';
      return Math.max(0, Math.min(100, numberValue)) + '%';
    }

    function resolvePeakMemoryKb(measured) {
      if (typeof measured.peakMemoryKb === 'number' && Number.isFinite(measured.peakMemoryKb)) {
        return measured.peakMemoryKb;
      }
      if (isRecord(measured.metrics) && typeof measured.metrics.peak_memory_kb === 'number') {
        return measured.metrics.peak_memory_kb;
      }
      return undefined;
    }

    function formatMemoryKb(kb) {
      const value = Number(kb);
      if (!Number.isFinite(value)) return '--';
      if (value >= 1024) return (value / 1024).toFixed(1) + ' MB';
      return Math.round(value) + ' KB';
    }

    function render() {
      try {
        inputEl.disabled = Boolean(state.loading);
        sendEl.disabled = Boolean(state.loading);
        statusEl.textContent = state.loading ? 'Planning approaches...' : (state.notice || state.error || '');
        statusEl.className = state.error ? 'composer-status error' : 'composer-status';
        renderRunBar();

        const nextMessages = document.createDocumentFragment();
        if (!state.messages || state.messages.length === 0) {
          const emptyState = document.createElement('div');
          emptyState.className = 'empty-state';
          emptyState.innerHTML = '<div style="width: 48px; height: 48px; border: 1px solid var(--border-strong); border-radius: 4px; display: flex; align-items: center; justify-content: center; margin-bottom: 16px; background: var(--surface-high);"><i class="svg-terminal" style="width: 24px; height: 24px; opacity: 0.8;"></i></div><div style="font-size: 15px; font-weight: 600; color: var(--text); margin-bottom: 8px;">Ready to build.</div><div style="font-size: 13px; color: var(--text-soft);">Describe the approach you\\'d like me to test.</div>';
          nextMessages.appendChild(emptyState);
        }
        for (const message of state.messages || []) {
          const node = document.createElement('div');
          node.className = 'msg ' + (message.role || 'assistant');
          if (message.role === 'user') {
            node.textContent = String(message.content || '');
          } else if (message.role === 'system') {
            node.textContent = String(message.content || '');
          } else {
            const content = document.createElement('div');
            content.className = 'msg-content';
            const dot = document.createElement('span');
            dot.className = 'agent-dot';
            const dotIcon = document.createElement('i');
            dotIcon.className = 'svg-sparkle';
            dot.appendChild(dotIcon);
            const text = document.createElement('div');
            text.textContent = String(message.content || '');
            content.appendChild(dot);
            content.appendChild(text);
            node.appendChild(content);
            if (Array.isArray(message.options) && message.options.length) {
              node.appendChild(renderCards(message.options));
            }
          }
          nextMessages.appendChild(node);
        }
        messagesEl.replaceChildren(nextMessages);
        messagesEl.scrollTop = messagesEl.scrollHeight;
      } catch (error) {
        console.error('Bench render failed', error);
        statusEl.textContent = 'Bench UI render failed. Check the extension host developer tools.';
        statusEl.className = 'composer-status error';
        if (!messagesEl.children.length) {
          const node = document.createElement('div');
          node.className = 'msg system';
          node.textContent = error && error.message ? error.message : String(error);
          messagesEl.appendChild(node);
        }
      }
    }

    function renderRunBar() {
      const hasOptions = (state.options || []).length >= 2;
      const isRunning = (state.runState && state.runState.status === 'running') || (state.options || []).some(o => o.runStatus === 'queued' || o.runStatus === 'running');
      const canApply = Boolean(state.runState && state.runState.winnerCandidateId);

      runBarEl.className = hasOptions ? 'run-bar visible' : 'run-bar';
      if (!hasOptions) {
        runBarEl.replaceChildren();
        return;
      }

      const label = (state.runState && state.runState.summary) || (isRunning ? 'Running tests...' : 'Ready to test approaches.');
      const icon = isRunning ? '<i class="svg-spin"></i>' : '';

      runBarEl.innerHTML = \`
        <div class="run-status">\${icon}\${escapeHtml(label)}</div>
        <div class="run-actions">
          <button class="outline-btn" data-action="testAll" \${isRunning ? 'disabled' : ''}>Test all</button>
          <button class="outline-btn" data-action="applyWinner" \${!canApply ? 'disabled' : ''}>Preview/apply winner</button>
        </div>
      \`;
    }

    function renderCards(options) {
      const wrap = document.createElement('div');
      wrap.className = 'cards';
      options.forEach((option, index) => {
        option = option || {};
        const status = String(option.runStatus || 'draft');
        const letter = String.fromCharCode(65 + index);
        const statusLabel = status.replace(/_/g, ' ');
        const isFailedStatus = status === 'failed' || status === 'error' || status === 'timeout';

        let iconClass = 'svg-draft';
        if (status === 'passed') iconClass = 'svg-check';
        else if (status === 'failed' || status === 'error' || status === 'timeout') iconClass = 'svg-alert';
        else if (status === 'running' || status === 'queued') iconClass = 'svg-spin';

        const card = document.createElement('div');
        card.className = 'card ' + status + (option.selected ? ' selected' : '');
        card.setAttribute('data-option-id', String(option.id || ''));

        const top = document.createElement('div');
        top.className = 'card-top';
        const titleWrap = document.createElement('div');
        titleWrap.className = 'card-title-wrap';
        const meta = document.createElement('div');
        meta.className = 'card-meta';
        const badge = document.createElement('span');
        badge.className = 'badge';
        badge.textContent = 'APPROACH ' + letter;
        const pill = document.createElement('span');
        pill.className = 'status-pill';
        pill.textContent = statusLabel;
        meta.appendChild(badge);
        meta.appendChild(pill);
        const title = document.createElement('span');
        title.className = 'card-title';
        title.textContent = String(option.title || 'Untitled option');
        titleWrap.appendChild(meta);
        titleWrap.appendChild(title);
        const statusIcon = document.createElement('div');
        statusIcon.className = 'status-icon';
        const statusIconInner = document.createElement('i');
        statusIconInner.className = iconClass;
        statusIcon.appendChild(statusIconInner);
        top.appendChild(titleWrap);
        top.appendChild(statusIcon);
        card.appendChild(top);

        if (option.summary) {
          const summary = document.createElement('div');
          summary.className = 'card-summary';
          summary.textContent = String(option.summary);
          card.appendChild(summary);
        }

        card.appendChild(renderMetrics(option, status));

        const actions = document.createElement('div');
        actions.className = 'card-actions';
        const leftActions = document.createElement('div');
        leftActions.className = 'left-actions';
        const isApplied = option.applyState === 'applied';
        const isPreviewed = option.applyState === 'previewed' || option.selected;
        const detailButton = document.createElement('button');
        detailButton.className = 'action-btn view-logs';
        detailButton.type = 'button';
        detailButton.dataset.action = 'viewLogs';
        const detailIcon = document.createElement('i');
        detailIcon.className = 'svg-' + (isFailedStatus ? 'logs' : 'metrics');
        detailButton.appendChild(detailIcon);
        detailButton.appendChild(document.createTextNode(' ' + (isFailedStatus ? 'View Logs' : 'Details')));
        leftActions.appendChild(detailButton);
        const selectButton = document.createElement('button');
        selectButton.className = 'select-btn';
        selectButton.type = 'button';
        selectButton.dataset.action = isPreviewed ? 'rejectPreview' : 'selectOption';
        selectButton.disabled = isApplied;
        selectButton.textContent = isApplied ? 'Applied' : (isPreviewed ? 'Reject' : 'Preview');
        actions.appendChild(leftActions);
        if (isPreviewed && !isApplied) {
          const applyButton = document.createElement('button');
          applyButton.className = 'select-btn';
          applyButton.type = 'button';
          applyButton.dataset.action = 'applySelected';
          applyButton.textContent = 'Apply';
          actions.appendChild(applyButton);
        }
        actions.appendChild(selectButton);
        card.appendChild(actions);
        if (option.applySummary) {
          const applySummary = document.createElement('div');
          applySummary.className = 'card-summary';
          applySummary.textContent = String(option.applySummary);
          card.appendChild(applySummary);
        }
        wrap.appendChild(card);
      });
      return wrap;
    }

    function renderMetrics(option, status) {
      const m = isRecord(option.measured) ? option.measured : {};
      const mock = isRecord(option.metrics) ? option.metrics : {};
      const tests = normalizeTests(m.tests);
      const isPending = status === 'queued' || status === 'running';
      const isFailed = status === 'failed' || status === 'error' || status === 'timeout';
      const pendingLabel = status === 'queued' ? 'Queued' : 'Running...';
      const peakMemoryKb = resolvePeakMemoryKb(m);

      let runVal = 'Not run';
      if (typeof m.durationMs === 'number') runVal = Math.round(m.durationMs) + 'ms';
      else if (isPending) runVal = pendingLabel;
      else if (isFailed) runVal = 'Failed';
      else if (mock.speed !== undefined) runVal = textOrEmpty(mock.speed) + ' (est)';

      let testVal = 'Not run';
      if (tests) testVal = tests.passed + '/' + tests.total + ' passed';
      else if (isPending) testVal = pendingLabel;
      else if (isFailed) testVal = 'Fail';
      else if (mock.testConfidence !== undefined) testVal = textOrEmpty(mock.testConfidence) + '% (est)';

      let memVal = 'Not run';
      if (peakMemoryKb !== undefined) memVal = formatMemoryKb(peakMemoryKb);
      else if (isPending) memVal = pendingLabel;
      else if (isFailed) memVal = 'Check logs';
      else if (mock.memory !== undefined) memVal = textOrEmpty(mock.memory) + ' (est)';

      let runTitle = 'Runtime';
      let testTitle = tests ? 'Tests (' + tests.passed + '/' + tests.total + ')' : 'Tests';
      let memTitle = 'Mem';

      let runPct = '0%';
      if (typeof m.durationMs === 'number') runPct = '100%';
      else if (status === 'queued') runPct = '12%';
      else if (status === 'running') runPct = '40%';
      else if (isFailed) runPct = '50%';
      else if (mock.speed !== undefined) runPct = percent(mock.speed);

      let testPct = '0%';
      if (tests && tests.total > 0) testPct = percent((tests.passed / tests.total) * 100);
      else if (status === 'queued') testPct = '12%';
      else if (status === 'running') testPct = '48%';
      else if (isFailed) testPct = '10%';
      else if (mock.testConfidence !== undefined) testPct = percent(mock.testConfidence);

      let memPct = '0%';
      if (peakMemoryKb !== undefined) memPct = '100%';
      else if (status === 'queued') memPct = '12%';
      else if (status === 'running') memPct = '20%';
      else if (isFailed) memPct = '100%';
      else if (mock.memory !== undefined) memPct = percent(mock.memory);

      const metrics = document.createElement('div');
      metrics.className = 'metrics';
      [
        [runTitle, runPct, runVal],
        [testTitle, testPct, testVal],
        [memTitle, memPct, memVal]
      ].forEach(([title, pct, value]) => {
        const col = document.createElement('div');
        col.className = 'metric-col';
        const label = document.createElement('span');
        label.className = 'metric-title';
        label.textContent = String(title);
        const barBg = document.createElement('div');
        barBg.className = 'metric-bar-bg';
        const barFg = document.createElement('div');
        barFg.className = 'metric-bar-fg';
        barFg.style.width = String(pct);
        barBg.appendChild(barFg);
        const val = document.createElement('span');
        val.className = 'metric-val';
        val.textContent = String(value);
        col.appendChild(label);
        col.appendChild(barBg);
        col.appendChild(val);
        metrics.appendChild(col);
      });
      return metrics;
    }

    function renderInlineDetails(option, status) {
      const m = option.measured || {};
      const tests = normalizeTests(m.tests);
      const duration = typeof m.durationMs === 'number' ? Math.round(m.durationMs) + 'ms' : '--';
      const testValue = tests ? \`\${tests.passed}/\${tests.total}\` : '--';
      const memoryValue = escapeHtml(formatMemoryKb(resolvePeakMemoryKb(m)));
      const plan = escapeHtml(option.implementationPlan || 'No implementation plan returned.');
      const tradeoffs = Array.isArray(option.tradeoffs) && option.tradeoffs.length
        ? option.tradeoffs.map((item) => '<li>' + escapeHtml(item) + '</li>').join('')
        : '<li>No tradeoffs returned.</li>';
      const issues = collectIssues(m);
      const issueHtml = issues.length
        ? '<div class="detail-section"><div class="detail-section-title">Failures / Logs</div><ul class="detail-list issue-list">' + issues.map((item) => '<li>' + escapeHtml(item) + '</li>').join('') + '</ul></div>'
        : '<div class="empty-detail">No failure logs for this option.</div>';

      return \`
        <div class="inline-details">
          <div class="detail-grid">
            <div class="detail-stat"><div class="detail-label">Duration</div><div class="detail-value">\${duration}</div></div>
            <div class="detail-stat"><div class="detail-label">Tests</div><div class="detail-value">\${testValue}</div></div>
            <div class="detail-stat"><div class="detail-label">Memory</div><div class="detail-value">\${memoryValue}</div></div>
          </div>
          <div class="detail-section">
            <div class="detail-section-title">Plan</div>
            <div>\${plan}</div>
          </div>
          <div class="detail-section">
            <div class="detail-section-title">Tradeoffs</div>
            <ul class="detail-list">\${tradeoffs}</ul>
          </div>
          \${status === 'failed' || status === 'error' || status === 'timeout' ? issueHtml : ''}
        </div>
      \`;
    }

    function collectIssues(measured) {
      const issues = [];
      for (const failure of measured.failures || []) {
        const test = failure.test ? failure.test + ': ' : '';
        issues.push(test + (failure.details || 'Test failed.'));
      }
      for (const error of measured.errors || []) {
        issues.push(JSON.stringify(error));
      }
      return issues;
    }

    function findOptionContext(optionId) {
      const allOptions = [];
      for (const message of state.messages || []) {
        if (Array.isArray(message.options)) {
          allOptions.push(...message.options);
        }
      }
      if (!allOptions.length && Array.isArray(state.options)) {
        allOptions.push(...state.options);
      }
      const index = allOptions.findIndex((option) => option.id === optionId);
      if (index < 0) {
        return undefined;
      }
      return { option: allOptions[index], index };
    }

    function openLogsPanel(optionId) {
      const context = findOptionContext(optionId);
      if (!context) {
        return;
      }
      const status = String(context.option.runStatus || 'draft');
      const isFailedStatus = status === 'failed' || status === 'error' || status === 'timeout';
      logsPanel = {
        optionId,
        optionIndex: context.index,
        activeTab: isFailedStatus ? 'logs' : 'details',
        logText: '',
        loading: true
      };
      logsOverlayEl.classList.add('open');
      logsOverlayEl.setAttribute('aria-hidden', 'false');
      logsTitleEl.textContent = 'Approach ' + String.fromCharCode(65 + context.index) + ' - Logs';
      setLogsTab(logsPanel.activeTab);
      refreshLogsPanelContent();
      renderLogsContent('Loading Docker logs...', 'system');
      vscode.postMessage({ type: 'viewLogs', optionId });
    }

    function closeLogsPanel() {
      logsOverlayEl.classList.remove('open');
      logsOverlayEl.setAttribute('aria-hidden', 'true');
      logsPanel = {
        optionId: undefined,
        optionIndex: 0,
        activeTab: 'logs',
        logText: '',
        loading: false
      };
    }

    function setLogsTab(tab) {
      logsPanel.activeTab = tab;
      document.querySelectorAll('.logs-tab').forEach((node) => {
        const isActive = node.getAttribute('data-tab') === tab;
        node.classList.toggle('active', isActive);
        node.setAttribute('aria-selected', isActive ? 'true' : 'false');
      });
      document.querySelectorAll('.logs-pane').forEach((node) => {
        node.classList.toggle('active', node.getAttribute('data-pane') === tab);
      });
    }

    function refreshLogsPanelContent() {
      const context = logsPanel.optionId ? findOptionContext(logsPanel.optionId) : undefined;
      if (!context) {
        return;
      }
      const option = context.option;
      const status = String(option.runStatus || 'draft');
      const detailsPane = document.querySelector('.logs-pane-details');
      const metricsPane = document.querySelector('.logs-pane-metrics');
      if (detailsPane) {
        detailsPane.innerHTML = renderInlineDetails(option, status);
      }
      if (metricsPane) {
        metricsPane.replaceChildren(renderMetrics(option, status));
      }
    }

    function handleLogsMessage(data) {
      if (!logsPanel.optionId || data.optionId !== logsPanel.optionId) {
        return;
      }
      logsPanel.loading = false;
      if (data.error) {
        logsPanel.logText = data.error;
        renderLogsContent(data.error, 'error');
        return;
      }
      if (data.notice) {
        logsPanel.logText = data.notice;
        renderLogsContent(data.notice, 'system');
        return;
      }
      logsPanel.logText = textOrEmpty(data.content);
      renderLogsContent(logsPanel.logText, 'plain');
    }

    function renderLogsContent(text, kind) {
      const logsPane = document.querySelector('.logs-pane-logs');
      if (!logsPane) {
        return;
      }
      if (!text) {
        logsPane.innerHTML = '<div class="logs-empty">No Docker logs returned for this candidate.</div>';
        return;
      }
      const terminal = document.createElement('div');
      terminal.className = 'logs-terminal';
      terminal.id = 'logsContent';
      const lines = String(text).split(/\\r?\\n/);
      lines.forEach((line, index) => {
        const row = document.createElement('span');
        row.className = 'log-line' + (kind === 'system' ? ' system' : kind === 'error' ? ' error' : classifyLogLine(line));
        row.textContent = line || (index < lines.length - 1 ? '' : '');
        terminal.appendChild(row);
        if (index < lines.length - 1) {
          terminal.appendChild(document.createTextNode('\\n'));
        }
      });
      logsPane.replaceChildren(terminal);
    }

    function classifyLogLine(line) {
      const trimmed = String(line || '').trim().toLowerCase();
      if (!trimmed) {
        return '';
      }
      if (trimmed.startsWith('[system]') || trimmed.includes('sandbox process exited')) {
        return ' system';
      }
      if (trimmed.includes('fail') || trimmed.includes('error') || trimmed.includes('traceback')) {
        return ' error';
      }
      if (trimmed.includes('passed') || trimmed.includes('ok')) {
        return ' success';
      }
      return '';
    }

    function clearLogsContent() {
      logsPanel.logText = '';
      renderLogsContent('', 'system');
    }

    async function copyLogsContent() {
      const text = logsPanel.logText || '';
      if (!text) {
        return;
      }
      try {
        if (navigator.clipboard && navigator.clipboard.writeText) {
          await navigator.clipboard.writeText(text);
        }
      } catch (error) {
        console.error('Bench copy logs failed', error);
      }
    }

    function escapeHtml(value) {
      return String(value ?? '').replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' }[char]));
    }

    vscode.postMessage({ type: 'ready' });
  </script>
</body>
</html>`;
  }

}

type WebviewMessage = {
  type:
    | "ready"
    | "askFeature"
    | "selectOption"
    | "applySelected"
    | "rejectPreview"
    | "newFeatureChat"
    | "testAll"
    | "applyWinner"
    | "viewLogs";
  text?: string;
  optionId?: string;
  openInEditor?: boolean;
};

type WebviewState = {
  messages: ChatMessage[];
  options: BenchOption[];
  selectedOptionId?: string;
  runState?: BenchRunState;
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

function buildFastApiAuthFallbackSuggestions(featureRequest: string): BenchOption[] {
  const slug = featureRequest.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 28) || "auth-endpoint";
  return [
    {
      id: `${slug}-dependency`,
      title: "Dependency-based bearer guard",
      summary: "Use a FastAPI dependency to centralize bearer-token validation for protected routes.",
      implementationPlan: "Define a reusable require_token dependency, attach it to /protected, and keep /health unauthenticated.",
      tradeoffs: ["Easy to reuse across routes", "Adds one indirection for a tiny API"],
      generatedCode: [
        "from fastapi import Depends, FastAPI, Header, HTTPException",
        "",
        "",
        "def require_token(authorization: str | None = Header(default=None)) -> None:",
        "    if authorization is None:",
        "        raise HTTPException(status_code=401, detail=\"Missing bearer token\")",
        "    if authorization != \"Bearer test-token\":",
        "        raise HTTPException(status_code=403, detail=\"Invalid bearer token\")",
        "",
        "",
        "def create_app() -> FastAPI:",
        "    app = FastAPI()",
        "",
        "    @app.get(\"/health\")",
        "    def health():",
        "        return {\"status\": \"ok\"}",
        "",
        "    @app.get(\"/protected\", dependencies=[Depends(require_token)])",
        "    def protected():",
        "        return {\"authenticated\": True, \"strategy\": \"dependency\"}",
        "",
        "    return app",
        ""
      ].join("\n"),
      runStatus: "draft",
      selected: false
    },
    {
      id: `${slug}-inline`,
      title: "Inline route validation",
      summary: "Keep the authentication rule directly inside the protected endpoint.",
      implementationPlan: "Read the Authorization header in /protected, branch on missing and invalid tokens, and return JSON for the valid token.",
      tradeoffs: ["Very explicit", "Duplicates logic if more protected routes are added"],
      generatedCode: [
        "from fastapi import FastAPI, Header, HTTPException",
        "",
        "",
        "def create_app() -> FastAPI:",
        "    app = FastAPI()",
        "",
        "    @app.get(\"/health\")",
        "    def health():",
        "        return {\"status\": \"ok\"}",
        "",
        "    @app.get(\"/protected\")",
        "    def protected(authorization: str | None = Header(default=None)):",
        "        if authorization is None:",
        "            raise HTTPException(status_code=401, detail=\"Missing bearer token\")",
        "        if authorization != \"Bearer test-token\":",
        "            raise HTTPException(status_code=403, detail=\"Invalid bearer token\")",
        "        return {\"authenticated\": True, \"strategy\": \"inline\"}",
        "",
        "    return app",
        ""
      ].join("\n"),
      runStatus: "draft",
      selected: false
    },
    {
      id: `${slug}-too-permissive`,
      title: "Permissive header check",
      summary: "A deliberately flawed option that proves the fixture catches wrong-token authorization bugs.",
      implementationPlan: "Require the header to exist but skip token value validation, which should fail the wrong-token test.",
      tradeoffs: ["Demonstrates failure evidence", "Not secure enough to ship"],
      generatedCode: [
        "from fastapi import FastAPI, Header, HTTPException",
        "",
        "",
        "def create_app() -> FastAPI:",
        "    app = FastAPI()",
        "",
        "    @app.get(\"/health\")",
        "    def health():",
        "        return {\"status\": \"ok\"}",
        "",
        "    @app.get(\"/protected\")",
        "    def protected(authorization: str | None = Header(default=None)):",
        "        if authorization is None:",
        "            raise HTTPException(status_code=401, detail=\"Missing bearer token\")",
        "        return {\"authenticated\": True, \"strategy\": \"too-permissive\"}",
        "",
        "    return app",
        ""
      ].join("\n"),
      runStatus: "draft",
      selected: false
    }
  ];
}

function buildPythonMergeFallbackSuggestions(featureRequest: string): BenchOption[] {
  const slug = featureRequest.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 28) || "python-merge";
  return [
    {
      id: `${slug}-readable`,
      title: "Readable sorted merge",
      summary: "Sort intervals by start point, then merge overlapping ranges in one explicit pass.",
      implementationPlan: "Normalize empty input, sort by start/end, append non-overlapping ranges, and extend the current range when intervals overlap.",
      tradeoffs: ["Easy to audit", "O(n log n) due to sorting"],
      generatedCode: [
        "def merge_intervals(intervals):",
        "    if not intervals:",
        "        return []",
        "    ordered = sorted(intervals, key=lambda item: (item[0], item[1]))",
        "    merged = []",
        "    for start, end in ordered:",
        "        if not merged or start > merged[-1][1]:",
        "            merged.append([start, end])",
        "        else:",
        "            merged[-1][1] = max(merged[-1][1], end)",
        "    return merged",
        ""
      ].join("\n"),
      metrics: {
        readability: 94,
        simplicity: 91,
        speed: 82,
        memory: 88,
        maintainability: 92,
        testConfidence: 86
      },
      runStatus: "draft",
      selected: false
    },
    {
      id: `${slug}-fast`,
      title: "Tuple copy merge",
      summary: "Use local variables and tuple unpacking to keep the merge loop compact and quick.",
      implementationPlan: "Sort a copied interval list, carry the active output interval, and mutate only the output copy.",
      tradeoffs: ["Avoids mutating caller input", "Slightly denser than the readable version"],
      generatedCode: [
        "def merge_intervals(intervals):",
        "    ordered = sorted(([start, end] for start, end in intervals), key=lambda item: item[0])",
        "    if not ordered:",
        "        return []",
        "    merged = [ordered[0]]",
        "    for start, end in ordered[1:]:",
        "        current = merged[-1]",
        "        if start <= current[1]:",
        "            if end > current[1]:",
        "                current[1] = end",
        "        else:",
        "            merged.append([start, end])",
        "    return merged",
        ""
      ].join("\n"),
      metrics: {
        readability: 82,
        simplicity: 80,
        speed: 90,
        memory: 84,
        maintainability: 85,
        testConfidence: 84
      },
      runStatus: "draft",
      selected: false
    },
    {
      id: `${slug}-edge-case-check`,
      title: "Intentional edge-case miss",
      summary: "A deliberately flawed candidate that demonstrates failure evidence and logs.",
      implementationPlan: "Return the input unchanged so the Docker fixture can surface failing tests and compare outcomes.",
      tradeoffs: ["Useful for proving result reporting", "Incorrect for overlapping intervals"],
      generatedCode: [
        "def merge_intervals(intervals):",
        "    return intervals",
        ""
      ].join("\n"),
      metrics: {
        readability: 70,
        simplicity: 96,
        speed: 96,
        memory: 92,
        maintainability: 42,
        testConfidence: 34
      },
      runStatus: "draft",
      selected: false
    }
  ];
}

function buildDecisionMessage(decision: DecisionPayload): string {
  const candidateLines = decision.candidates.map((candidate) => {
    const tests = candidate.tests ? `${candidate.tests.passed}/${candidate.tests.total} tests` : "tests unavailable";
    const duration = typeof candidate.duration_ms === "number" ? `${Math.round(candidate.duration_ms)}ms` : "duration unavailable";
    return `${candidate.label}: ${candidate.status}, ${tests}, ${duration}`;
  });
  const winner = decision.winner_candidate_id ? ` Winner: ${decision.winner_candidate_id}.` : "";
  return [
    `Docker evaluation finished for run ${decision.run_id}.${winner}`,
    decision.summary ?? "No summary returned by the daemon.",
    `Decision Payload returned to the extension: ${candidateLines.join(" | ")}`
  ].join(" ");
}

function buildDecisionLogEntry(decision: DecisionPayload): string {
  const candidateLines = decision.candidates.map((candidate) => {
    const tests = candidate.tests ? `${candidate.tests.passed}/${candidate.tests.total}` : "tests unavailable";
    const duration = typeof candidate.duration_ms === "number" ? `${Math.round(candidate.duration_ms)}ms` : "duration unavailable";
    return `${candidate.label}=${candidate.status} (${tests}, ${duration})`;
  });
  return [
    `Docker fixture ${decision.fixture_id} completed.`,
    decision.winner_candidate_id ? `Winner candidate: ${decision.winner_candidate_id}.` : "No winner candidate.",
    decision.summary ?? "No summary.",
    `Evidence: ${candidateLines.join("; ")}`
  ].join(" ");
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
      runStatus: "draft",
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
      runStatus: "draft",
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
      runStatus: "draft",
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
