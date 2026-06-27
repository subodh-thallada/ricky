import { BenchFixtureId, BenchOption, CandidateMeasurement, CandidateRunStatus } from "../types";
import { CandidateSpec, DaemonClient, DaemonEvent, DecisionPayload, RunCreateRequest } from "./daemonClient";
import { ensureLocalService } from "./localServiceManager";
import { MOCK_SHOP_MODULES, MockShopModule } from "../fixtures/mockShopReference.generated";

export type CandidateUpdate = {
  optionId: string;
  candidateId: string;
  runId?: string;
  status: CandidateRunStatus;
  measured?: CandidateMeasurement;
  logsUrl?: string;
  codeUrl?: string;
};

export type RunCallbacks = {
  onRunCreated(update: { runId: string; options: CandidateUpdate[] }): void;
  onCandidateUpdate(update: CandidateUpdate): void;
  onDecision(payload: DecisionPayload, updates: CandidateUpdate[]): void;
};

// Where each fixture expects the candidate code to land inside its workspace. Each
// mock-shop module fixture targets one file in the mock_shop package; the others
// take a single flat candidate file.
const FIXTURE_TARGET_FILE: Record<BenchFixtureId, string> = {
  "python-merge": "candidate_target.py",
  "fastapi-auth-endpoint": "candidate_target.py",
  "mock-shop-auth": "mock_shop/auth.py",
  "mock-shop-checkout": "mock_shop/checkout.py",
  "mock-shop-payments": "mock_shop/payments.py",
  "mock-shop-db": "mock_shop/db.py",
  "mock-shop-models": "mock_shop/models.py"
};

// Registry-driven lookup for the mock-shop module fixtures (auth/checkout/payments/
// db/models). One fixture per module; detection + routing key off this generated
// registry instead of hardcoding the auth module, so a checkout candidate runs
// against the checkout fixture rather than being force-swapped to auth.
const MOCK_SHOP_BY_FIXTURE: Map<BenchFixtureId, MockShopModule> = new Map(
  MOCK_SHOP_MODULES.map((mod) => [mod.fixtureId as BenchFixtureId, mod])
);

export const MOCK_SHOP_FIXTURES: MockShopModule[] = MOCK_SHOP_MODULES;

export function isMockShopFixture(fixtureId: BenchFixtureId): boolean {
  return MOCK_SHOP_BY_FIXTURE.has(fixtureId);
}

export function mockShopModuleForFixture(fixtureId: BenchFixtureId): MockShopModule | undefined {
  return MOCK_SHOP_BY_FIXTURE.get(fixtureId);
}

export class DaemonSandboxRunner {
  constructor(private readonly client = new DaemonClient()) {}

  validatePythonMergeOptions(options: BenchOption[]): BenchOption[] {
    return options.filter((option) => isPythonMergeImplementation(option.generatedCode));
  }

  validateFastApiAuthOptions(options: BenchOption[]): BenchOption[] {
    return options.filter((option) => isFastApiAuthImplementation(option.generatedCode));
  }

  validateMockShopOptions(options: BenchOption[], fixtureId: BenchFixtureId): BenchOption[] {
    const mod = MOCK_SHOP_BY_FIXTURE.get(fixtureId);
    if (!mod) {
      return [];
    }
    return options.filter((option) => matchesMockShopModule(option.generatedCode, mod));
  }

  async runPythonMerge(options: BenchOption[], callbacks: RunCallbacks, signal?: AbortSignal): Promise<DecisionPayload> {
    return this.runFixture(
      "python-merge",
      this.validatePythonMergeOptions(options),
      "Need at least two Python options defining merge_intervals(intervals) before running the python-merge fixture.",
      callbacks,
      signal
    );
  }

  async runFastApiAuthEndpoint(options: BenchOption[], callbacks: RunCallbacks, signal?: AbortSignal): Promise<DecisionPayload> {
    return this.runFixture(
      "fastapi-auth-endpoint",
      this.validateFastApiAuthOptions(options),
      "Need at least two Python FastAPI options defining create_app() before running the fastapi-auth-endpoint fixture.",
      callbacks,
      signal
    );
  }

  async runMockShop(
    fixtureId: BenchFixtureId,
    options: BenchOption[],
    callbacks: RunCallbacks,
    signal?: AbortSignal
  ): Promise<DecisionPayload> {
    const mod = MOCK_SHOP_BY_FIXTURE.get(fixtureId);
    const target = mod?.targetFile ?? FIXTURE_TARGET_FILE[fixtureId];
    return this.runFixture(
      fixtureId,
      this.validateMockShopOptions(options, fixtureId),
      `Need at least two Python options that rewrite ${target} before running the ${mod?.label ?? fixtureId} fixture.`,
      callbacks,
      signal
    );
  }

  private async runFixture(
    fixtureId: BenchFixtureId,
    validOptions: BenchOption[],
    validationError: string,
    callbacks: RunCallbacks,
    signal?: AbortSignal
  ): Promise<DecisionPayload> {
    if (validOptions.length < 2) {
      throw new Error(validationError);
    }
    await ensureLocalService({
      label: "Bench daemon",
      baseUrl: this.client.baseUrl(),
      command: process.platform === "win32" ? ".venv/Scripts/python.exe" : ".venv/bin/python",
      args: ["-m", "bench_daemon", "serve", "--host", "127.0.0.1", "--port", "8001"],
      logFile: ".bench-logs/daemon.log"
    });
    const health = await this.client.health();
    if (health.status !== "ok") {
      throw new Error(`Bench daemon health check failed: status=${health.status}`);
    }
    if (health.docker_available === false) {
      throw new Error("Bench daemon is running, but Docker is not available to the daemon process.");
    }

    const mapped = mapOptionsToCandidates(validOptions, fixtureId);
    const request: RunCreateRequest = {
      fixture_id: fixtureId,
      rebuild_image: false,
      candidates: mapped.candidates
    };
    const run = await this.client.createRun(request);
    callbacks.onRunCreated({
      runId: run.run_id,
      options: mapped.optionCandidatePairs.map((pair) => ({
        optionId: pair.optionId,
        candidateId: pair.candidateId,
        runId: run.run_id,
        status: "queued"
      }))
    });

    let lastSequence = 0;
    await this.client.streamEvents(
      run.events_url,
      (event) => {
        if (event.sequence !== undefined && event.sequence <= lastSequence) {
          return;
        }
        if (event.sequence !== undefined) {
          lastSequence = event.sequence;
        }
        const update = eventToCandidateUpdate(event, mapped.candidateToOptionId, run.run_id);
        if (update) {
          callbacks.onCandidateUpdate(update);
        }
      },
      signal
    );

    const payload = await this.client.getRun(run.result_url);
    const updates = decisionToUpdates(payload, mapped.candidateToOptionId, this.client);
    callbacks.onDecision(payload, updates);
    return payload;
  }

  async fetchText(pathOrUrl: string): Promise<string> {
    return this.client.getText(pathOrUrl);
  }
}

function mapOptionsToCandidates(options: BenchOption[], fixtureId: BenchFixtureId): {
  candidates: CandidateSpec[];
  optionCandidatePairs: Array<{ optionId: string; candidateId: string }>;
  candidateToOptionId: Map<string, string>;
} {
  const targetFile = FIXTURE_TARGET_FILE[fixtureId];
  const usedIds = new Set<string>();
  const optionCandidatePairs: Array<{ optionId: string; candidateId: string }> = [];
  const candidateToOptionId = new Map<string, string>();
  const candidates = options.map((option, index) => {
    const candidateId = uniqueCandidateId(option.id, index, usedIds);
    optionCandidatePairs.push({ optionId: option.id, candidateId });
    candidateToOptionId.set(candidateId, option.id);
    return {
      candidate_id: candidateId,
      label: option.title || `Candidate ${index + 1}`,
      rationale: buildRationale(option),
      files: buildCandidateFiles(fixtureId, targetFile, option.generatedCode)
    };
  });

  return { candidates, optionCandidatePairs, candidateToOptionId };
}

// Mock-shop modules live inside the mock_shop package; map any generated section
// onto that package by basename so the candidate overlays the real workspace.
const MOCK_SHOP_PACKAGE = "mock_shop";

// Turn an option's generatedCode into the file map the daemon overlays onto the
// fixture workspace. Most fixtures take a single target file verbatim; mock-shop
// parses "### path" + fenced sections so a candidate can rewrite mock_shop/auth.py
// (and optionally add sibling modules like mock_shop/rate_limit.py).
function buildCandidateFiles(
  fixtureId: BenchFixtureId,
  targetFile: string,
  generatedCode: string
): Record<string, string> {
  if (!MOCK_SHOP_BY_FIXTURE.has(fixtureId)) {
    return { [targetFile]: generatedCode };
  }

  const files: Record<string, string> = {};
  const sections = parseFileSections(generatedCode);
  for (const section of sections) {
    const base = section.path.split("/").pop() ?? "";
    if (!base.endsWith(".py")) {
      continue;
    }
    files[`${MOCK_SHOP_PACKAGE}/${base}`] = section.code;
  }

  if (!files[targetFile]) {
    // No auth.py section was captured — treat the whole snippet (joined sections
    // or the raw, fence-stripped body) as the auth module.
    const flat = sections.length
      ? sections.map((section) => section.code).join("\n\n")
      : stripCodeFences(generatedCode);
    files[targetFile] = flat.trim() ? flat : generatedCode;
  }
  return files;
}

type FileSection = { path: string; code: string };

function parseFileSections(text: string): FileSection[] {
  const sections: FileSection[] = [];
  // "### relative/path.ext" followed by a fenced code block.
  const headed = /(?:^|\n)###\s*([^\n`]+?)\s*\n+```[\w-]*\n([\s\S]*?)```/g;
  let match: RegExpExecArray | null;
  while ((match = headed.exec(text)) !== null) {
    sections.push({ path: match[1].trim(), code: normalizeCode(match[2]) });
  }
  if (sections.length) {
    return sections;
  }
  // No path headers: collect bare fenced blocks (path unknown).
  const bare = /```[\w-]*\n([\s\S]*?)```/g;
  while ((match = bare.exec(text)) !== null) {
    sections.push({ path: "", code: normalizeCode(match[1]) });
  }
  return sections;
}

function stripCodeFences(text: string): string {
  const fenced = /^```[\w-]*\n([\s\S]*?)```\s*$/m.exec(text.trim());
  return fenced ? normalizeCode(fenced[1]) : text;
}

function normalizeCode(code: string): string {
  return `${code.replace(/\s+$/, "")}\n`;
}

function uniqueCandidateId(optionId: string, index: number, usedIds: Set<string>): string {
  const base = optionId.replace(/[^A-Za-z0-9_-]+/g, "_").replace(/^_+|_+$/g, "") || `candidate_${index + 1}`;
  let candidateId = base.slice(0, 64);
  let suffix = 2;
  while (usedIds.has(candidateId)) {
    candidateId = `${base.slice(0, 58)}_${suffix}`;
    suffix += 1;
  }
  usedIds.add(candidateId);
  return candidateId;
}

function buildRationale(option: BenchOption): string {
  const tradeoffs = option.tradeoffs.length ? ` Tradeoffs: ${option.tradeoffs.slice(0, 2).join("; ")}.` : "";
  return `${option.summary}${tradeoffs}`.trim();
}

function isPythonMergeImplementation(code: string): boolean {
  return /^\s*def\s+merge_intervals\s*\(\s*intervals\s*[\),]/m.test(code);
}

function isFastApiAuthImplementation(code: string): boolean {
  return (
    /^\s*def\s+create_app\s*\(\s*\)\s*(?:->[^:]+)?:/m.test(code) &&
    /\bFastAPI\b/.test(code) &&
    /\/protected/.test(code)
  );
}

// A mock-shop candidate matches a module fixture when, after extracting files, the
// module's target file defines that module's required public surface (auth -> login
// + signup, checkout -> checkout + cart_total_cents, etc.). Detection is data-driven
// from the generated registry, so adding a module fixture needs no change here.
function matchesMockShopModule(code: string, mod: MockShopModule): boolean {
  const target =
    buildCandidateFiles(mod.fixtureId as BenchFixtureId, mod.targetFile, code)[mod.targetFile] ?? "";
  return mod.requiredDefs.every((source) => new RegExp(source).test(target));
}

function eventToCandidateUpdate(event: DaemonEvent, candidateToOptionId: Map<string, string>, runId: string): CandidateUpdate | undefined {
  const candidateId = typeof event.data.candidate_id === "string" ? event.data.candidate_id : undefined;
  if (!candidateId) {
    return undefined;
  }

  const optionId = candidateToOptionId.get(candidateId);
  if (!optionId) {
    return undefined;
  }

  const status = typeof event.data.status === "string" ? event.data.status : undefined;
  if (!isCandidateRunStatus(status)) {
    return undefined;
  }

  return {
    optionId,
    candidateId,
    runId,
    status,
    measured: isTerminalStatus(status) ? measurementFromEvent(status, event.data) : undefined
  };
}

function decisionToUpdates(payload: DecisionPayload, candidateToOptionId: Map<string, string>, client: DaemonClient): CandidateUpdate[] {
  return payload.candidates.flatMap((candidate) => {
    const optionId = candidateToOptionId.get(candidate.candidate_id);
    if (!optionId) {
      return [];
    }
    return [
      {
        optionId,
        candidateId: candidate.candidate_id,
        runId: payload.run_id,
        status: candidate.status,
        measured: {
          status: candidate.status,
          exitCode: candidate.exit_code,
          durationMs: candidate.duration_ms,
          tests: candidate.tests,
          failures: normalizeFailures(candidate.failures),
          errors: candidate.errors,
          metrics: candidate.metrics
        },
        logsUrl: client.resolveUrl(candidate.logs_url),
        codeUrl: client.resolveUrl(candidate.code_url)
      }
    ];
  });
}

function measurementFromEvent(status: "passed" | "failed" | "timeout" | "error", data: Record<string, unknown>): CandidateMeasurement {
  return {
    status,
    exitCode: typeof data.exit_code === "number" ? data.exit_code : null,
    durationMs: typeof data.duration_ms === "number" ? data.duration_ms : null,
    tests: isTests(data.tests) ? data.tests : null,
    failures: normalizeFailures(data.failures),
    errors: Array.isArray(data.errors) ? data.errors.filter(isRecord) : undefined,
    metrics: isRecord(data.metrics) ? data.metrics : undefined
  };
}

function normalizeFailures(value: unknown): Array<{ test?: string; details?: string }> | undefined {
  if (!Array.isArray(value)) {
    return undefined;
  }
  return value.filter(isRecord).map((failure) => ({
    test: typeof failure.test === "string" ? failure.test : undefined,
    details: typeof failure.details === "string" ? failure.details : JSON.stringify(failure)
  }));
}

function isTests(value: unknown): value is { passed: number; failed: number; total: number } {
  return (
    isRecord(value) &&
    typeof value.passed === "number" &&
    typeof value.failed === "number" &&
    typeof value.total === "number"
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isTerminalStatus(status: CandidateRunStatus): status is "passed" | "failed" | "timeout" | "error" {
  return status === "passed" || status === "failed" || status === "timeout" || status === "error";
}

function isCandidateRunStatus(status: string | undefined): status is CandidateRunStatus {
  return status === "queued" || status === "running" || status === "passed" || status === "failed" || status === "timeout" || status === "error";
}
