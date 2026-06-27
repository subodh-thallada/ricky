import * as vscode from "vscode";
import { BenchSuggestion, WorkspaceContext } from "../types";

type CerebrasChatChoice = {
  message?: {
    content?: string;
  };
};

type CerebrasChatResponse = {
  choices?: CerebrasChatChoice[];
};

export class CerebrasClient {
  constructor(private readonly secrets: vscode.SecretStorage) {}

  async generateSuggestions(featureRequest: string, workspaceContext: WorkspaceContext): Promise<BenchSuggestion[]> {
    const apiKey = await this.getApiKey();
    if (!apiKey) {
      throw new Error("Missing Cerebras API key. Run Bench: Set Cerebras API Key.");
    }

    const model = vscode.workspace.getConfiguration("bench").get<string>("cerebrasModel")?.trim()
      || "zai-glm-4.7";

    const response = await fetch("https://api.cerebras.ai/v1/chat/completions", {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${apiKey}`,
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        model,
        temperature: 0.35,
        max_tokens: 2600,
        messages: [
          {
            role: "system",
            content: [
              "You are Bench, a VS Code coding assistant.",
              "Return at least 2 and at most 3 genuinely different implementation options when real tradeoffs exist.",
              "Each option must include a concise title, summary, implementation plan, tradeoffs, and generated code.",
              "Inside generatedCode, use workspace-relative file sections formatted as ### path followed by a fenced code block.",
              "You may create new files or change multiple files when needed, but prefer the smallest useful file set.",
              "Match existing symbols and stubs so Bench can place edits safely instead of duplicating surrounding code.",
              "Return only valid JSON. Do not wrap the response in Markdown. Do not include commentary outside JSON.",
              "JSON shape: {\"suggestions\":[{\"id\":\"stable-kebab-id\",\"title\":\"...\",\"summary\":\"...\",\"implementationPlan\":\"...\",\"tradeoffs\":[\"...\"],\"generatedCode\":\"...\"}]}"
            ].join(" ")
          },
          {
            role: "user",
            content: JSON.stringify({
              featureRequest,
              workspaceContext
            })
          }
        ]
      })
    });

    if (!response.ok) {
      const errorText = await response.text();
      throw new Error(`Cerebras request failed: ${response.status} ${errorText.slice(0, 280)}`);
    }

    const payload = await response.json() as CerebrasChatResponse;
    const content = payload.choices?.[0]?.message?.content;
    if (!content) {
      throw new Error("Cerebras returned an empty response.");
    }

    return normalizeSuggestions(parseSuggestions(content));
  }

  private async getApiKey(): Promise<string | undefined> {
    const secret = await this.secrets.get("bench.cerebrasApiKey");
    if (secret?.trim()) {
      return secret.trim();
    }

    const configured = vscode.workspace.getConfiguration("bench").get<string>("cerebrasApiKey");
    if (configured?.trim()) {
      return configured.trim();
    }

    return process.env.CEREBRAS_API_KEY?.trim();
  }
}

function parseSuggestions(content: string): BenchSuggestion[] {
  const cleaned = stripOuterJsonFence(content);

  const parsed = JSON.parse(cleaned) as { suggestions?: unknown };
  if (!Array.isArray(parsed.suggestions)) {
    throw new Error("Cerebras response did not contain a suggestions array.");
  }

  return parsed.suggestions.map((item, index) => {
    const value = item as Partial<BenchSuggestion>;
    return {
      id: typeof value.id === "string" && value.id.trim() ? value.id.trim() : `option-${index + 1}`,
      title: typeof value.title === "string" && value.title.trim() ? value.title.trim() : `Option ${index + 1}`,
      summary: typeof value.summary === "string" ? value.summary : "",
      implementationPlan: typeof value.implementationPlan === "string" ? value.implementationPlan : "",
      tradeoffs: Array.isArray(value.tradeoffs) ? value.tradeoffs.map(String) : [],
      generatedCode: typeof value.generatedCode === "string" ? value.generatedCode : ""
    };
  });
}

function normalizeSuggestions(suggestions: BenchSuggestion[]): BenchSuggestion[] {
  const usable = suggestions.filter((suggestion) => suggestion.title && suggestion.generatedCode);
  if (usable.length === 0) {
    throw new Error("Cerebras returned no usable implementation options.");
  }

  return usable.slice(0, 3).map((suggestion, index) => ({
    ...suggestion,
    id: suggestion.id || `option-${index + 1}`
  }));
}

function stripOuterJsonFence(content: string): string {
  const cleaned = content.trim();
  const match = cleaned.match(/^```(?:json)?\s*([\s\S]*?)\s*```$/i);
  if (match?.[1]) {
    return match[1].trim();
  }
  return cleaned;
}
