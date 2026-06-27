import * as vscode from "vscode";
import { BenchOption, WorkspaceContext } from "../types";

export type FeatureOptionsResponse = {
  assistantMessage: string;
  contextSummary: string;
  contextMetadata: Record<string, unknown>;
  geminiModel: string;
  cerebrasModel: string;
  options: Array<Omit<BenchOption, "selected" | "applyState" | "applySummary">>;
};

export class OrchestratorClient {
  async generateFeatureOptions(prompt: string, workspaceContext: WorkspaceContext): Promise<{ message: string; options: BenchOption[]; contextSummary: string }> {
    const baseUrl = vscode.workspace.getConfiguration("bench").get<string>("orchestratorUrl")?.replace(/\/$/, "") || "http://127.0.0.1:8000";
    const workspaceRoot = vscode.workspace.workspaceFolders?.[0];
    const activeRelativePath = workspaceContext.activeFileName && workspaceRoot
      ? vscode.workspace.asRelativePath(workspaceContext.activeFileName, false)
      : undefined;
    const response = await fetch(`${baseUrl}/feature-options`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        prompt,
        language: workspaceContext.languageId || "typescript",
        active_file_name: workspaceContext.activeFileName,
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

    const payload = await response.json() as FeatureOptionsResponse;
    return {
      message: payload.assistantMessage,
      contextSummary: payload.contextSummary,
      options: payload.options.map((option) => ({
        ...option,
        selected: false,
        applyState: "idle"
      }))
    };
  }
}
