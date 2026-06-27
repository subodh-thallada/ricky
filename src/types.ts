export type BenchSuggestion = {
  id: string;
  title: string;
  summary: string;
  implementationPlan: string;
  tradeoffs: string[];
  generatedCode: string;
};

export type BenchMetricSet = {
  readability: number;
  simplicity: number;
  speed: number;
  memory: number;
  maintainability: number;
  testConfidence: number;
};

export type BenchOption = BenchSuggestion & {
  metrics: BenchMetricSet;
  selected: boolean;
  applyState: "idle" | "previewed" | "applied";
  applySummary?: string;
};

export type ChatMessage = {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  options?: BenchOption[];
};

export type WorkspaceContext = {
  activeFileName?: string;
  languageId?: string;
  selectedText?: string;
  visibleText?: string;
};

export interface MetricsProvider {
  attachMetrics(suggestions: BenchSuggestion[]): BenchOption[];
}

export interface SandboxRunner {
  run(_options: BenchOption[]): Promise<BenchOption[]>;
}

export type ApplyPreviewResult = {
  optionId: string;
  fileCount: number;
  summary: string;
};

export type ApplyResult = {
  optionId: string;
  fileCount: number;
  summary: string;
};

export interface ApplyProvider {
  preview(option: BenchOption, workspaceContext: WorkspaceContext): Promise<ApplyPreviewResult>;
  applySelected(): Promise<ApplyResult | undefined>;
  rejectSelected(): Promise<string | undefined>;
  getPendingOptionId(): string | undefined;
}
