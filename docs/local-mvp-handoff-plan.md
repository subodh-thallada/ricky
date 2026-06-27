# Bench Local MVP Handoff Plan

## Goal

Build the local Bench loop that powers the VS Code prototype:

1. A calling surface starts a run.
2. The local daemon loads a test fixture.
3. Each candidate runs in its own Docker container.
4. The daemon ranks results.
5. The calling surface receives a compact Decision Payload.

This handoff intentionally does not implement Cursor, Claude Code, or other agent adapters. Bench chat is the eventual calling surface; until that is ready, a VS Code command, demo panel, or CLI can call the same daemon API.

## Existing Artifacts

Domain language:

- [CONTEXT.md](/Users/naveed/ricky/CONTEXT.md)

Product spec:

- [bench-product-spec.md](/Users/naveed/ricky/bench-product-spec.md)

Local test fixture:

- [bench.json](/Users/naveed/ricky/fixtures/python-merge/bench.json)
- [Dockerfile](/Users/naveed/ricky/fixtures/python-merge/Dockerfile)
- [candidate_target.py](/Users/naveed/ricky/fixtures/python-merge/candidate_target.py)
- [test_candidate.py](/Users/naveed/ricky/fixtures/python-merge/test_candidate.py)
- [bench_runner.py](/Users/naveed/ricky/fixtures/python-merge/bench_runner.py)
- [readable.py](/Users/naveed/ricky/fixtures/python-merge/candidates/readable.py)
- [fast.py](/Users/naveed/ricky/fixtures/python-merge/candidates/fast.py)
- [broken_edge_case.py](/Users/naveed/ricky/fixtures/python-merge/candidates/broken_edge_case.py)
- [slow.py](/Users/naveed/ricky/fixtures/python-merge/candidates/slow.py)

## Fixture Contract

Each fixture owns its runtime environment and runner config:

```json
{
  "id": "python-merge",
  "label": "Merge intervals",
  "language": "python",
  "target_file": "candidate_target.py",
  "runner": "python bench_runner.py",
  "dockerfile": "Dockerfile",
  "docker_context": ".",
  "docker_image": "bench-fixture-python-merge:local",
  "timeout_ms": 5000,
  "candidates_dir": "candidates"
}
```

For the local MVP, candidates are full replacements for `target_file`. A Candidate may include additional relative text files, but patch-based editing comes later.

## Decision Payload

Return summaries inline. Keep full logs and code behind detail endpoints.

```json
{
  "run_id": "run_123",
  "winner_candidate_id": "readable",
  "summary": "Readable passed all tests and was fastest among passing candidates.",
  "candidates": [
    {
      "candidate_id": "readable",
      "label": "Readable",
      "status": "passed",
      "exit_code": 0,
      "duration_ms": 0.633,
      "tests": {"passed": 8, "failed": 0, "total": 8},
      "logs_url": "/runs/run_123/candidates/readable/logs",
      "code_url": "/runs/run_123/candidates/readable/code"
    }
  ],
  "recommended_next_action": "Return evidence to coding agent",
  "available_actions": [
    {"action": "return_evidence", "label": "Return evidence to coding agent"},
    {"action": "apply_candidate", "label": "Offer apply winner", "candidate_id": "readable"}
  ]
}
```

`recommended_next_action` is backend-facing: return measured evidence to the coding agent or calling surface. If the ranked winner passed, `available_actions` also exposes an `apply_candidate` action for the calling surface to render. The daemon does not apply code to the real workspace.

## Daemon API

Implement this first as a local FastAPI app.

```text
GET  /health
GET  /fixtures
POST /runs
GET  /runs/{run_id}
GET  /runs/{run_id}/events
GET  /runs/{run_id}/candidates/{candidate_id}/logs
GET  /runs/{run_id}/candidates/{candidate_id}/code
POST /maintenance/clear-local-runs
```

`POST /runs` request:

```json
{
  "fixture_id": "python-merge",
  "rebuild_image": false,
  "candidates": null
}
```

If `candidates` is null, load all files from `candidates_dir`. Later, the calling surface can provide generated candidates:

```json
{
  "candidate_id": "readable",
  "label": "Readable",
  "rationale": "Clear sort-and-sweep implementation.",
  "files": {
    "candidate_target.py": "def merge_intervals(intervals):\n    ..."
  }
}
```

`POST /runs` is asynchronous. It returns a `run_id`, `events_url`, and `result_url`; callers poll `GET /runs/{run_id}` or subscribe to `GET /runs/{run_id}/events` until the run reaches `completed` or `failed`. The completed run snapshot is the Decision Payload.

The code detail endpoint returns the exact replacement files supplied for that Candidate. A single-file fixture returns the target file content; multi-file candidates are returned as a deterministic text bundle with one section per relative path.

## Agent-Facing Commands

Start the daemon:

```bash
python3 -m bench_daemon serve --host 127.0.0.1 --port 8000
```

Run the prewritten no-network fixture Candidates and print the final Decision Payload:

```bash
python3 -m bench_daemon run --fixture-id python-merge
```

Submit explicit Candidate replacement files from a request JSON file:

```bash
python3 -m bench_daemon run --request-json docs/candidate-evaluation-request.example.json
```

Equivalent raw API call:

```bash
curl -s -X POST http://127.0.0.1:8000/runs \
  -H 'Content-Type: application/json' \
  --data @docs/candidate-evaluation-request.example.json
```

Then poll the returned `result_url`, for example:

```bash
curl -s http://127.0.0.1:8000/runs/run_123
```

## Run Algorithm

1. Load `bench.json`.
2. Validate `target_file`, `runner`, `dockerfile`, and `candidates_dir`.
3. Build or reuse the fixture Docker image:
   - Check image with `docker image inspect <docker_image>`.
   - If missing or `rebuild_image: true`, run `docker build -f <dockerfile> -t <docker_image> <docker_context>`.
4. Create one run workspace under OS temp:
   - Example: `/tmp/bench/runs/<run_id>/<candidate_id>/`
5. For each candidate:
   - Copy fixture files into candidate workspace.
   - Exclude the `candidates/` directory from the runtime workspace.
   - Write the candidate file into `target_file`.
   - Run Docker:

```bash
docker run --rm \
  --network none \
  --memory 256m \
  --cpus 1.0 \
  --pids-limit 128 \
  -v /tmp/bench/runs/<run_id>/<candidate_id>:/work:ro \
  -w /work \
  bench-fixture-python-merge:local \
  sh -lc "python bench_runner.py"
```

6. Enforce `timeout_ms` from the fixture config.
7. Parse the final stdout line as JSON.
8. Treat all earlier stdout plus stderr as logs.
9. Store run snapshots in memory.
10. Stream events to the calling surface.
11. Rank candidates and emit the Decision Payload.

## Event Stream

Use Server-Sent Events for the local MVP.

Event names:

```text
run_started
image_build_skipped
image_build_started
image_build_finished
candidate_queued
candidate_running
candidate_finished
run_completed
run_failed
```

Example candidate event:

```json
{
  "event": "candidate_finished",
  "run_id": "run_123",
  "candidate_id": "slow",
  "status": "passed",
  "duration_ms": 6.673,
  "tests": {"passed": 8, "failed": 0, "total": 8},
  "failures": [],
  "errors": [],
  "metrics": {"runtime_p95_ms": 6.1}
}
```

## Ranking Rule

Local MVP ranking:

1. `passed`
2. `failed`
3. `timeout`
4. `error`

Within the same status, shorter `duration_ms` wins.

The expected fixture behavior is:

- `broken_edge_case`: fails `test_empty_input`
- `readable`: passes
- `fast`: passes
- `slow`: passes, but slower

## Temp Cleanup

Use OS temp:

```text
/tmp/bench/runs/<run_id>/<candidate_id>/
```

Cleanup policy:

- Passed candidate workspaces: delete after capturing result.
- Failed/error/timeout workspaces: keep for the VS Code session.
- `POST /maintenance/clear-local-runs`: delete retained temp workspaces.

## Extension Integration

The extension should not manage Docker directly.

1. User clicks `Test all`.
2. Extension calls `GET /health`.
3. If not running, extension starts the local daemon process.
4. Extension calls `POST /runs`.
5. Extension subscribes to `GET /runs/{run_id}/events`.
6. Extension updates cards and peek rail from events.
7. Extension fetches `GET /runs/{run_id}` for the final Decision Payload.
8. Extension returns that payload to the calling surface.

Until Bench chat is ready, a demo command or simple webview can render the same payload.

## Implementation Order

1. Create `bench_daemon/` Python package.
2. Implement fixture loading and validation.
3. Implement Docker image build/cache.
4. Implement candidate workspace creation.
5. Implement Docker candidate execution with timeout.
6. Implement final JSON-line parsing from `bench_runner.py`.
7. Implement in-memory run state.
8. Implement ranking and Decision Payload creation.
9. Implement FastAPI endpoints.
10. Implement SSE events.
11. Implement temp cleanup command.
12. Wire a VS Code command or demo panel to call the daemon.
13. Later: replace prewritten candidates with Cerebras-generated candidates.
14. Later: move from Test Fixture to App Fixture.

## Verification Commands

Verify the fixture runner without Docker:

```bash
cd /Users/naveed/ricky/fixtures/python-merge
python3 bench_runner.py
```

Expected: 8 passed tests and final JSON line.

Verify all candidate files without Docker:

```bash
python3 - <<'PY'
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

fixture = Path('/Users/naveed/ricky/fixtures/python-merge')
for candidate in sorted((fixture / 'candidates').glob('*.py')):
    with tempfile.TemporaryDirectory() as temp:
        work = Path(temp) / 'work'
        shutil.copytree(fixture, work, ignore=shutil.ignore_patterns('candidates'))
        shutil.copy2(candidate, work / 'candidate_target.py')
        proc = subprocess.run(
            ['python3', 'bench_runner.py'],
            cwd=work,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        final = json.loads(proc.stdout.strip().splitlines()[-1])
        print(json.dumps({
            'candidate': candidate.stem,
            'exit_code': proc.returncode,
            'tests': final['tests'],
            'duration_ms': final['duration_ms'],
        }, sort_keys=True))
PY
```

Expected:

```text
broken_edge_case exits 1 with 7 passed / 1 failed
fast exits 0 with 8 passed
readable exits 0 with 8 passed
slow exits 0 with 8 passed and slower duration
```

Docker verification comes after daemon implementation:

```bash
docker build \
  -f /Users/naveed/ricky/fixtures/python-merge/Dockerfile \
  -t bench-fixture-python-merge:local \
  /Users/naveed/ricky/fixtures/python-merge
```

## Acceptance Criteria

- A fresh clone can run the fixture tests locally with stdlib Python.
- The daemon can build or reuse the fixture Docker image.
- `POST /runs` starts four candidate runs in parallel with max concurrency 4.
- `broken_edge_case` fails and does not block the other candidates.
- The daemon returns a compact Decision Payload with `logs_url` and `code_url`.
- The event stream updates candidate status as work progresses.
- Passed workspaces are cleaned up; failed workspaces are inspectable.
- The extension or demo calling surface can render the final payload.

## Handoff Prompt

Use this prompt for a fresh implementation chat:

```text
Implement the Bench local MVP runner using /Users/naveed/ricky/docs/local-mvp-handoff-plan.md as the source of truth. Start by building a Python FastAPI daemon that loads /Users/naveed/ricky/fixtures/python-merge/bench.json, builds/caches its Docker image, runs each candidate from candidates/ in parallel with max concurrency 4, parses the final JSON line from bench_runner.py, streams SSE events, and returns the compact Decision Payload. Do not implement Cursor or Claude Code adapters. If Bench chat is unavailable, expose a CLI or simple demo endpoint that can call POST /runs and print the Decision Payload.
```
