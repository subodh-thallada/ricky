# Bench

Bench is a VS Code extension plus local orchestrator for comparing implementation options before you choose one.

The current MVP does this:

- VS Code side-panel chatbot for feature requests.
- Gemini handles the user-facing chat, codebase context condensation, implementation planning, and mock metrics.
- Cerebras writes code only, using Gemini's condensed context and implementation plans.
- The extension shows suggestion cards and a side details panel for metrics/code/plan/tradeoffs.
- **Test all** sends compatible cards to the local Bench daemon for Docker-backed candidate evaluation.
- Cards update with measured pass/fail status, test counts, duration, and log links.
- Selecting or applying a winner only marks it as selected. It does not edit workspace files yet.

Backboard taste updates and workspace patching are future hooks.

## VS Code Extension

1. Run `npm install`.
2. Run `npm run compile`.
3. Press `F5` in VS Code and choose **Run Bench Extension**.
4. In the Extension Development Host, open **Bench** from the Activity Bar.
5. Ask Bench to build a feature.

The extension calls the local orchestrator at `http://127.0.0.1:8000` and the local daemon at `http://127.0.0.1:8001` by default. You can change these with `bench.orchestratorUrl` and `bench.daemonUrl`.

## Orchestrator

1. Create a virtual environment.
2. Install dependencies with `pip install -e .`
3. Copy `.env.example` to `.env` and fill in your keys.
4. Run `uvicorn bench.main:app --reload --host 127.0.0.1 --port 8000`
5. Run `python -m bench.scripts.check_providers`

Required for the MVP flow:

- `GEMINI_API_KEY`
- `CEREBRAS_API_KEY`

## Bench Daemon

Start Docker Desktop first, then run the daemon on its own port:

```bash
python3 -m bench_daemon serve --host 127.0.0.1 --port 8001
```

Verify the Docker path independently:

```bash
python3 -m bench_daemon run --base-url http://127.0.0.1:8001 --fixture-id python-merge
```

The Phase 0 demo uses the `python-merge` fixture. If generated cards do not define `merge_intervals(intervals)`, the extension loads known-good Python demo cards and still runs real Docker evidence through the daemon.

## Provider Split

- Gemini: chat response, condensed repository context, option plans, tradeoffs, and initial mock metrics.
- Cerebras: generated code only.
- Bench daemon: Docker execution, ranking, logs, and Decision Payload.

This keeps chat and code generation separate from local sandbox execution.
