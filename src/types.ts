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
  metrics?: BenchMetricSet | null;
  candidateId?: string;
  runId?: string;
  runStatus: CandidateRunStatus;
  measured?: CandidateMeasurement;
  logsUrl?: string;
  codeUrl?: string;
  selected: boolean;
  applyState?: "idle" | "previewed" | "applied";
  applySummary?: string;
};

export type CandidateRunStatus =
  | "draft"
  | "queued"
  | "running"
  | "passed"
  | "failed"
  | "timeout"
  | "error";

export type CandidateMeasurement = {
  status: "passed" | "failed" | "timeout" | "error";
  exitCode?: number | null;
  durationMs?: number | null;
  tests?: { passed: number; failed: number; total: number } | null;
  failures?: Array<{ test?: string; details?: string }>;
  errors?: Array<Record<string, unknown>>;
  metrics?: Record<string, unknown>;
};

export type BenchRunState = {
  runId: string;
  fixtureId: BenchFixtureId;
  status: "queued" | "running" | "completed" | "failed";
  winnerCandidateId?: string | null;
  summary?: string | null;
};

// mock-shop is no longer a single fixture: every module in the mock_shop package
// (auth, checkout, payments, db, models) is its own Docker fixture targeting one
// file, so a candidate is tested against the module it actually rewrites instead of
// being force-routed to auth. Keep this union in sync with fixtures/mock-shop-*/
// (the generated registry in src/fixtures/mockShopReference.generated.ts is the
// data source; this union gives the compiler the closed set).
export type BenchFixtureId =
  | "python-merge"
  | "fastapi-auth-endpoint"
  | "mock-shop-auth"
  | "mock-shop-checkout"
  | "mock-shop-payments"
  | "mock-shop-db"
  | "mock-shop-models";

export type ChatMessage = {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  options?: BenchOption[];
  appliedOptionId?: string;
  appliedOptionTitle?: string;
  appliedSummary?: string;
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
  deactivatedSessionIds?: string[];
};

export type ApplyResult = {
  optionId: string;
  fileCount: number;
  summary: string;
};

export interface ApplyProvider {
  preview(sessionId: string, option: BenchOption, workspaceContext: WorkspaceContext): Promise<ApplyPreviewResult>;
  applySelected(sessionId: string): Promise<ApplyResult | undefined>;
  rejectSelected(sessionId: string): Promise<string | undefined>;
  hasPendingSession(sessionId: string): boolean;
}
