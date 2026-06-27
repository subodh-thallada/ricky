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

export interface ApplyProvider {
  select(option: BenchOption): Promise<void>;
}
