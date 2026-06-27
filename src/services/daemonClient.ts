import * as vscode from "vscode";
import { BenchFixtureId } from "../types";

export type CandidateSpec = {
  candidate_id: string;
  label: string;
  rationale?: string;
  files: Record<string, string>;
};

export type RunCreateRequest = {
  fixture_id: BenchFixtureId;
  rebuild_image: boolean;
  candidates: CandidateSpec[];
};

export type RunCreateResponse = {
  run_id: string;
  status: string;
  events_url: string;
  result_url: string;
};

export type DaemonCandidatePayload = {
  candidate_id: string;
  label: string;
  status: "passed" | "failed" | "timeout" | "error";
  exit_code: number | null;
  duration_ms: number | null;
  tests: { passed: number; failed: number; total: number } | null;
  failures?: Array<Record<string, unknown>>;
  errors?: Array<Record<string, unknown>>;
  metrics?: Record<string, unknown>;
  logs_url: string;
  code_url: string;
};

export type DecisionPayload = {
  run_id: string;
  fixture_id: string;
  status: "queued" | "running" | "completed" | "failed";
  winner_candidate_id: string | null;
  summary: string | null;
  candidates: DaemonCandidatePayload[];
  available_actions: Array<{ action: string; label: string; candidate_id?: string }>;
  error?: string;
};

export type DaemonHealth = {
  status: string;
  docker_available?: boolean;
};

export type DaemonEvent = {
  event: string;
  data: Record<string, unknown>;
  sequence?: number;
};

export class DaemonClient {
  baseUrl(): string {
    return vscode.workspace.getConfiguration("bench").get<string>("daemonUrl")?.replace(/\/$/, "") || "http://127.0.0.1:8001";
  }

  async health(): Promise<DaemonHealth> {
    return this.getJson<DaemonHealth>("/health");
  }

  async createRun(request: RunCreateRequest): Promise<RunCreateResponse> {
    const response = await fetch(this.resolveUrl("/runs"), {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify(request)
    });
    return readJsonResponse<RunCreateResponse>(response, "Bench daemon run creation failed");
  }

  async getRun(runIdOrUrl: string): Promise<DecisionPayload> {
    const path = runIdOrUrl.startsWith("/") ? runIdOrUrl : `/runs/${encodeURIComponent(runIdOrUrl)}`;
    return this.getJson<DecisionPayload>(path);
  }

  async getText(pathOrUrl: string): Promise<string> {
    const response = await fetch(this.resolveUrl(pathOrUrl));
    if (!response.ok) {
      const text = await response.text();
      throw new Error(`Bench daemon text endpoint failed: ${response.status} ${text.slice(0, 280)}`);
    }
    return response.text();
  }

  async streamEvents(pathOrUrl: string, onEvent: (event: DaemonEvent) => void, signal?: AbortSignal): Promise<void> {
    const response = await fetch(this.resolveUrl(pathOrUrl), {
      headers: {
        Accept: "text/event-stream"
      },
      signal
    });
    if (!response.ok) {
      const text = await response.text();
      throw new Error(`Bench daemon event stream failed: ${response.status} ${text.slice(0, 280)}`);
    }
    if (!response.body) {
      throw new Error("Bench daemon event stream did not include a response body.");
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { value, done } = await reader.read();
      if (done) {
        break;
      }
      buffer += decoder.decode(value, { stream: true });
      const frames = buffer.split(/\r?\n\r?\n/);
      buffer = frames.pop() ?? "";
      for (const frame of frames) {
        const event = parseSseFrame(frame);
        if (event) {
          onEvent(event);
        }
      }
    }

    buffer += decoder.decode();
    const event = parseSseFrame(buffer);
    if (event) {
      onEvent(event);
    }
  }

  resolveUrl(pathOrUrl: string): string {
    if (/^https?:\/\//i.test(pathOrUrl)) {
      return pathOrUrl;
    }
    const path = pathOrUrl.startsWith("/") ? pathOrUrl : `/${pathOrUrl}`;
    return `${this.baseUrl()}${path}`;
  }

  private async getJson<T>(pathOrUrl: string): Promise<T> {
    const response = await fetch(this.resolveUrl(pathOrUrl));
    return readJsonResponse<T>(response, "Bench daemon request failed");
  }
}

async function readJsonResponse<T>(response: Response, message: string): Promise<T> {
  if (!response.ok) {
    const text = await response.text();
    throw new Error(`${message}: ${response.status} ${text.slice(0, 280)}`);
  }
  return response.json() as Promise<T>;
}

function parseSseFrame(frame: string): DaemonEvent | undefined {
  const trimmed = frame.trim();
  if (!trimmed || trimmed.startsWith(":")) {
    return undefined;
  }

  let event = "message";
  const dataLines: string[] = [];
  for (const line of frame.split(/\r?\n/)) {
    if (line.startsWith("event:")) {
      event = line.slice("event:".length).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice("data:".length).trimStart());
    }
  }

  if (dataLines.length === 0) {
    return undefined;
  }

  const data = JSON.parse(dataLines.join("\n")) as Record<string, unknown>;
  const sequence = typeof data.sequence === "number" ? data.sequence : undefined;
  return { event, data, sequence };
}
