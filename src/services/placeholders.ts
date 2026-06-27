import { BenchOption, SandboxRunner } from "../types";

export class NoopSandboxRunner implements SandboxRunner {
  async run(options: BenchOption[]): Promise<BenchOption[]> {
    return options;
  }
}
