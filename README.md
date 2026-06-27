# Bench

Bench is a VS Code extension plus local orchestrator for comparing implementation options before you choose one.

The current MVP does this:

- VS Code side-panel chatbot for feature requests.
- Gemini handles the user-facing chat, codebase context condensation, implementation planning, and mock metrics.
- Cerebras writes code only, using Gemini's condensed context and implementation plans.
- The extension shows suggestion cards and a side details panel for metrics/code/plan/tradeoffs.
- Selecting an option only marks it as selected. It does not edit workspace files yet.

Docker execution, real measurements, Backboard taste updates, and workspace patching are future hooks.

## VS Code Extension

1. Run `npm install`.
2. Run `npm run compile`.
3. Press `F5` in VS Code and choose **Run Bench Extension**.
4. In the Extension Development Host, open **Bench** from the Activity Bar.
5. Ask Bench to build a feature.

The extension calls the local orchestrator at `http://127.0.0.1:8000` by default. You can change this with the `bench.orchestratorUrl` setting.

## Orchestrator

1. Create a virtual environment.
2. Install dependencies with `pip install -e .`
3. Copy `.env.example` to `.env` and fill in your keys.
4. Run `uvicorn bench.main:app --reload`
5. Run `python -m bench.scripts.check_providers`

Required for the MVP flow:

- `GEMINI_API_KEY`
- `CEREBRAS_API_KEY`

## Provider Split

- Gemini: chat response, condensed repository context, option plans, tradeoffs, and mock metrics.
- Cerebras: generated code only.

This keeps the extension aligned with the eventual product architecture while avoiding Docker and real metrics until those are ready.
