import { BenchMetricSet, BenchOption, BenchSuggestion, MetricsProvider } from "../types";

const metricKeys: Array<keyof BenchMetricSet> = [
  "readability",
  "simplicity",
  "speed",
  "memory",
  "maintainability",
  "testConfidence"
];

export class MockMetricsProvider implements MetricsProvider {
  attachMetrics(suggestions: BenchSuggestion[]): BenchOption[] {
    return suggestions.map((suggestion, index) => ({
      ...suggestion,
      selected: false,
      metrics: this.buildMetrics(suggestion, index)
    }));
  }

  private buildMetrics(suggestion: BenchSuggestion, index: number): BenchMetricSet {
    const seed = this.hash(`${suggestion.title}:${suggestion.summary}:${index}`);
    const metrics = {} as BenchMetricSet;

    metricKeys.forEach((key, keyIndex) => {
      metrics[key] = 56 + ((seed + index * 17 + keyIndex * 23) % 39);
    });

    if (/simple|read|idiom|maintain/i.test(suggestion.title + suggestion.summary)) {
      metrics.readability = Math.min(96, metrics.readability + 10);
      metrics.simplicity = Math.min(96, metrics.simplicity + 8);
    }

    if (/fast|performance|cache|batch|parallel/i.test(suggestion.title + suggestion.summary)) {
      metrics.speed = Math.min(97, metrics.speed + 12);
      metrics.memory = Math.max(48, metrics.memory - 7);
    }

    return metrics;
  }

  private hash(input: string): number {
    let value = 0;
    for (let i = 0; i < input.length; i += 1) {
      value = (value * 31 + input.charCodeAt(i)) >>> 0;
    }
    return value;
  }
}
