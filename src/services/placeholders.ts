import { ApplyProvider, BenchOption, SandboxRunner } from "../types";

export class NoopSandboxRunner implements SandboxRunner {
  async run(options: BenchOption[]): Promise<BenchOption[]> {
    return options;
  }
}

export class SelectionOnlyApplyProvider implements ApplyProvider {
  async select(_option: BenchOption): Promise<void> {
    return;
  }
}
