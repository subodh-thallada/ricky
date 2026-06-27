import { ChildProcess, spawn } from "child_process";
import * as fs from "fs";
import * as path from "path";
import * as vscode from "vscode";

type LocalServiceSpec = {
  label: string;
  baseUrl: string;
  command: string;
  args: string[];
  logFile: string;
};

const startedServices = new Set<string>();
const childProcesses = new Map<string, ChildProcess>();

export async function ensureLocalService(spec: LocalServiceSpec): Promise<void> {
  if (!isLoopbackUrl(spec.baseUrl)) {
    return;
  }

  if (await isHealthy(spec.baseUrl)) {
    return;
  }

  if (!startedServices.has(spec.baseUrl)) {
    startService(spec);
    startedServices.add(spec.baseUrl);
  }

  const healthy = await waitForHealth(spec.baseUrl, 12_000);
  if (!healthy) {
    throw new Error(`${spec.label} is not reachable at ${spec.baseUrl}. Check ${spec.logFile} for startup logs.`);
  }
}

export function workspaceRoot(): string | undefined {
  return vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? extensionRoot();
}

async function isHealthy(baseUrl: string): Promise<boolean> {
  try {
    const response = await fetch(`${baseUrl.replace(/\/$/, "")}/health`);
    return response.ok;
  } catch {
    return false;
  }
}

async function waitForHealth(baseUrl: string, timeoutMs: number): Promise<boolean> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await isHealthy(baseUrl)) {
      return true;
    }
    await delay(300);
  }
  return false;
}

function startService(spec: LocalServiceSpec): void {
  const root = workspaceRoot();
  if (!root) {
    throw new Error(`Cannot start ${spec.label}: could not resolve the Bench extension root.`);
  }

  const logsDir = path.join(root, ".bench-logs");
  fs.mkdirSync(logsDir, { recursive: true });
  const logPath = path.join(root, spec.logFile);
  const output = fs.openSync(logPath, "a");
  const child = spawn(path.join(root, spec.command), spec.args, {
    cwd: root,
    detached: false,
    stdio: ["ignore", output, output]
  });
  fs.closeSync(output);
  childProcesses.set(spec.baseUrl, child);
  child.once("error", (error) => {
    fs.appendFileSync(logPath, `\n[bench extension] failed to start ${spec.label}: ${error.message}\n`);
    startedServices.delete(spec.baseUrl);
    childProcesses.delete(spec.baseUrl);
  });
  child.once("exit", (code, signal) => {
    fs.appendFileSync(logPath, `\n[bench extension] ${spec.label} exited code=${code ?? "null"} signal=${signal ?? "null"}\n`);
    startedServices.delete(spec.baseUrl);
    childProcesses.delete(spec.baseUrl);
  });
}

function extensionRoot(): string | undefined {
  const candidate = path.resolve(__dirname, "../..");
  if (fs.existsSync(path.join(candidate, "package.json"))) {
    return candidate;
  }
  return undefined;
}

function isLoopbackUrl(baseUrl: string): boolean {
  try {
    const url = new URL(baseUrl);
    return url.hostname === "127.0.0.1" || url.hostname === "localhost";
  } catch {
    return false;
  }
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
