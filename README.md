# Bench

Bench is a VS Code extension plus local orchestrator for comparing implementation options before you choose one.

The current MVP does this:

- VS Code side-panel chatbot for feature requests.
- Gemini handles the user-facing chat, codebase context condensation, implementation planning, and mock metrics.
- Cerebras writes code only, using Gemini's condensed context and implementation plans.
- The extension shows suggestion cards and a side details panel for metrics/code/plan/tradeoffs.
- Selecting an option only marks it as selected. It does not edit workspace files yet.
- A local `bench-daemon` can evaluate supplied candidate files against a fixture and return a compact decision payload.

App fixture execution, Backboard taste updates, and workspace patching are future hooks.

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

## Candidate Evaluation Daemon

The local daemon runs supplied candidate implementations against a fixture and
returns the measured decision payload used by the calling surface.

Start the daemon:

```bash
bench-daemon serve --host 127.0.0.1 --port 8000
```

List fixtures:

```bash
bench-daemon fixtures
```

Run the default `python-merge` fixture:

```bash
bench-daemon run --fixture-id python-merge
```

Run an explicit candidate evaluation request:

```bash
bench-daemon run --request-json docs/candidate-evaluation-request.example.json
```

The first fixture evaluates replacement files for `candidate_target.py`,
keeps candidate results isolated, and exposes logs and executed code through
the daemon API.

## Provider Split

- Gemini: chat response, condensed repository context, option plans, tradeoffs, and mock metrics.
- Cerebras: generated code only.

This keeps the extension aligned with the eventual product architecture while avoiding Docker and real metrics until those are ready.
