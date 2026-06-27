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
  async rememberFeatureDecision(featureRequest: string, selectedOptionId: string, options: BenchOption[]): Promise<void> {
    const baseUrl = this.baseUrl();
    const response = await fetch(`${baseUrl}/feature-options/decisions`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        featureRequest,
        selectedOptionId,
        options: options.map(({ selected, applyState, applySummary, ...option }) => option)
      })
    });

    if (!response.ok) {
      const errorText = await response.text();
      throw new Error(`Bench memory save failed: ${response.status} ${errorText.slice(0, 280)}`);
    }
  }

  async generateFeatureOptions(prompt: string, workspaceContext: WorkspaceContext): Promise<{ message: string; options: BenchOption[]; contextSummary: string }> {
    const baseUrl = this.baseUrl();
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
        repo_context: vscode.workspace.workspaceFolders?.[0]
          ? {
              root_path: vscode.workspace.workspaceFolders[0].uri.fsPath,
              query: prompt,
              focus_paths: workspaceContext.activeFileName ? [workspaceContext.activeFileName] : []
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

  private baseUrl(): string {
    return vscode.workspace.getConfiguration("bench").get<string>("orchestratorUrl")?.replace(/\/$/, "") || "http://127.0.0.1:8000";
  }
}
