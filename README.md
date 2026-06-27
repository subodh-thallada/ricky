# Bench VS Code Extension

Bench is a Copilot-style feature planning chat for VS Code. For this MVP, Cerebras generates implementation options and Bench attaches mock metrics. Docker execution, real measurements, and applying the selected option to the workspace are intentionally left as future extension points.

## Run Locally

1. Run `npm install`.
2. Run `npm run compile`.
3. Press `F5` in VS Code and choose **Run Bench Extension**.
4. In the Extension Development Host, open **Bench** from the Activity Bar.
5. Run **Bench: Set Cerebras API Key** from the command palette and paste your key.
6. Ask Bench to build a feature.

## Current Behavior

- Uses Cerebras chat completions to generate 3-4 implementation suggestions.
- Includes active file, language, selection, and visible editor text as optional context.
- Shows suggestion cards in a Bench chat side panel.
- Opens a side details panel for mock metrics, generated code, implementation plan, and tradeoffs.
- Selecting an option only marks it as selected. It does not edit the workspace.

## Future Hooks

The MVP has placeholder interfaces for:

- `MetricsProvider`
- `SandboxRunner`
- `ApplyProvider`

`MockMetricsProvider`, `NoopSandboxRunner`, and `SelectionOnlyApplyProvider` can be replaced later with Docker-backed measurements and workspace edits.
