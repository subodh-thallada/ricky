import * as vscode from "vscode";
import { BenchMetricSet, BenchOption, CandidateMeasurement, CandidateRunStatus, WorkspaceContext } from "../types";
import { ensureLocalService } from "./localServiceManager";

export type FeatureOptionsResponse = {
  assistantMessage?: unknown;
  contextSummary?: unknown;
  contextMetadata?: unknown;
  geminiModel?: unknown;
  cerebrasModel?: unknown;
  options?: unknown[];
};

export class OrchestratorClient {
  async generateFeatureOptions(prompt: string, workspaceContext: WorkspaceContext): Promise<{ message: string; options: BenchOption[]; contextSummary: string }> {
    const baseUrl = vscode.workspace.getConfiguration("bench").get<string>("orchestratorUrl")?.replace(/\/$/, "") || "http://127.0.0.1:8000";
    const workspaceRoot = vscode.workspace.workspaceFolders?.[0];
    const activeRelativePath = workspaceContext.activeFileName && workspaceRoot
      ? vscode.workspace.asRelativePath(workspaceContext.activeFileName, false)
      : undefined;
    await ensureLocalService({
      label: "Bench orchestrator",
      baseUrl,
      command: process.platform === "win32" ? ".venv/Scripts/python.exe" : ".venv/bin/python",
      args: ["-m", "uvicorn", "bench.main:app", "--host", "127.0.0.1", "--port", "8000"],
      logFile: ".bench-logs/orchestrator.log"
    });
    const response = await fetch(`${baseUrl}/feature-options`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        prompt,
        language: workspaceContext.languageId ?? "typescript",
        active_file_name: activeRelativePath,
        selected_text: workspaceContext.selectedText,
        visible_text: workspaceContext.visibleText,
        repo_context: workspaceRoot
          ? {
              root_path: workspaceRoot.uri.fsPath,
              query: prompt,
              focus_paths: activeRelativePath ? [activeRelativePath] : []
            }
          : undefined
      })
    });

    if (!response.ok) {
      const errorText = await response.text();
      throw new Error(`Bench orchestrator failed: ${response.status} ${errorText.slice(0, 280)}`);
    }

    const payload = await response.json() as Partial<FeatureOptionsResponse>;
    const rawOptions = Array.isArray(payload.options) ? payload.options : [];
    return {
      message: textField(payload.assistantMessage),
      contextSummary: textField(payload.contextSummary),
      options: rawOptions.map(normalizeOption)
    };
  }
}

function normalizeOption(raw: unknown, index: number): BenchOption {
  const source = isRecord(raw) ? raw : {};
  const metrics = isRecord(source.metrics) ? normalizeMetrics(source.metrics) : undefined;
  return {
    id: textField(source.id) || `option-${index + 1}`,
    title: textField(source.title) || `Option ${index + 1}`,
    summary: textField(source.summary),
    implementationPlan: textField(source.implementationPlan) || textField(source.implementation_plan),
    tradeoffs: Array.isArray(source.tradeoffs) ? source.tradeoffs.map(textField).filter(Boolean) : [],
    generatedCode: textField(source.generatedCode) || textField(source.generated_code),
    metrics,
    candidateId: textField(source.candidateId),
    runId: textField(source.runId),
    runStatus: normalizeRunStatus(source.runStatus),
    measured: isRecord(source.measured) ? source.measured as CandidateMeasurement : undefined,
    logsUrl: textField(source.logsUrl),
    codeUrl: textField(source.codeUrl),
    selected: Boolean(source.selected),
    applyState: normalizeApplyState(source.applyState),
    applySummary: textField(source.applySummary) || undefined
  };
}

function normalizeMetrics(raw: Record<string, unknown>): BenchMetricSet | undefined {
  const metrics = {
    readability: numberField(raw.readability),
    simplicity: numberField(raw.simplicity),
    speed: numberField(raw.speed),
    memory: numberField(raw.memory),
    maintainability: numberField(raw.maintainability),
    testConfidence: numberField(raw.testConfidence ?? raw.test_confidence)
  };
  return Object.values(metrics).every((value) => typeof value === "number") ? metrics as BenchMetricSet : undefined;
}

function normalizeRunStatus(value: unknown): CandidateRunStatus {
  return isCandidateRunStatus(value) ? value : "draft";
}

function normalizeApplyState(value: unknown): BenchOption["applyState"] {
  return value === "previewed" || value === "applied" ? value : "idle";
}

function isCandidateRunStatus(value: unknown): value is CandidateRunStatus {
  return (
    value === "draft" ||
    value === "queued" ||
    value === "running" ||
    value === "passed" ||
    value === "failed" ||
    value === "timeout" ||
    value === "error"
  );
}

function textField(value: unknown): string {
  return value === undefined || value === null ? "" : String(value);
}

function numberField(value: unknown): number | undefined {
  const numberValue = typeof value === "number" ? value : Number(value);
  return Number.isFinite(numberValue) ? numberValue : undefined;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}
