# Candidate Evaluation Orchestration Plan

## Purpose

This document describes who orchestrates the end-to-end flow once Bench is connected to a real application, a Calling Surface, and a Coding Agent.

The current backend already owns the container evaluation loop. The missing future layer is the orchestration that turns agent-proposed implementations from a live repo into a Candidate Evaluation Request, then returns the Decision Payload back to the agent or UI.

## Short Answer

There are two orchestrators:

1. **Calling Surface Orchestrator**
   - Lives in the VS Code extension, MCP adapter, CLI adapter, or future agent integration.
   - Captures candidate implementations from the agent or user.
   - Determines the target fixture/app target.
   - Builds the Candidate Evaluation Request.
   - Submits it to the local Bench daemon.
   - Streams progress and returns the final Decision Payload to the agent/UI.

2. **Bench Daemon Orchestrator**
   - Lives in `bench_daemon`.
   - Owns Docker, workspace materialization, container execution, timeouts, logs, parsed stats, cleanup, ranking, events, and detail endpoints.
   - Never asks the Calling Surface to manage Docker.

The Coding Agent proposes code and consumes evidence. It should not orchestrate Docker directly.

## End-To-End Flow

```text
Coding Agent proposes N implementations
-> Calling Surface Orchestrator captures those implementations
-> Calling Surface maps them to target file replacements or patches
-> Calling Surface sends Candidate Evaluation Request to Bench daemon
-> Bench daemon creates one isolated workspace per Candidate
-> Bench daemon runs the same focused fixture/app runner in Docker for each Candidate
-> Runner starts/probes only the relevant app/API behavior
-> Runner emits logs plus one final structured JSON result
-> Bench daemon parses results, ranks Candidates, retains/cleans workspaces
-> Bench daemon exposes final Decision Payload by run id
-> Calling Surface returns compact evidence to the Coding Agent
-> Calling Surface may offer Apply Winner if a passing winner exists
```

## Responsibilities By Component

### Coding Agent

The Coding Agent is responsible for:

- Proposing one or more candidate implementations.
- Preserving candidate labels/rationales where available.
- Reading the returned Decision Payload.
- Continuing its reasoning with measured evidence.

The Coding Agent is not responsible for:

- Running Docker.
- Creating temp workspaces.
- Knowing fixture Docker images.
- Ranking raw test output.
- Applying the winning code directly unless a separate adapter explicitly implements that workflow.

### Calling Surface Orchestrator

The Calling Surface may be:

- VS Code side panel.
- VS Code command.
- MCP server/tool adapter.
- CLI adapter.
- Future Claude Code/Cursor/Pi adapter.

It is responsible for:

- Capturing the current repo snapshot context.
- Capturing the candidate code or patch set.
- Selecting the target fixture/app target id.
- Converting candidates into the backend request shape.
- Calling `POST /runs`.
- Subscribing to `GET /runs/{run_id}/events` or polling `GET /runs/{run_id}`.
- Rendering or forwarding the final Decision Payload.
- Offering `apply_candidate` only if the payload exposes that action.

It is not responsible for:

- Building Docker images.
- Running candidate commands.
- Parsing runner output.
- Cleaning temp workspaces.

### Bench Daemon Orchestrator

The daemon is responsible for:

- Validating Candidate Evaluation Requests.
- Loading fixture/app target metadata.
- Building or reusing Docker images.
- Creating isolated workspaces from the same source snapshot.
- Applying each Candidate into its own workspace.
- Running each Candidate in a separate Docker container.
- Enforcing timeout/resource limits.
- Capturing stdout/stderr logs.
- Parsing the runner's final structured JSON line.
- Returning Candidate-level statuses for failures/timeouts/errors.
- Ranking results deterministically.
- Exposing logs and exact executed code by detail URL.
- Cleaning passed workspaces and retaining failed/error/timeout workspaces until maintenance cleanup.

## Candidate Evaluation Request

For the current backend, the Calling Surface sends a request like:

```json
{
  "fixture_id": "user-api-validation",
  "rebuild_image": false,
  "candidates": [
    {
      "candidate_id": "schema_first",
      "label": "Schema First",
      "rationale": "Use a shared validation schema before route logic.",
      "files": {
        "src/routes/users.ts": "..."
      }
    }
  ]
}
```

For future real-app work, this should evolve in one of two ways:

- **Fixture-backed app target:** the target app is represented under `fixtures/<app-id>/`, and candidates replace files inside that fixture snapshot.
- **Live repo app target:** the request references a source snapshot created from the current workspace, and candidates are applied to that snapshot.

The backend contract should remain the same conceptually: one request creates one run, and one run produces one Decision Payload.

## Focused API/App Runner

The runner defines what "done" means for a candidate. For an API endpoint, do not run the whole repo unless broad regression coverage is intentional.

Example focused runner behavior:

```text
start app in test mode
wait for health endpoint
POST /api/users with valid payload -> expect 201
POST /api/users with missing email -> expect 400
POST /api/users with invalid email -> expect 400
measure duration/latency
stop app
print final JSON result
```

The final line printed by the runner must be structured JSON:

```json
{
  "tests": {"passed": 3, "failed": 0, "total": 3},
  "failures": [],
  "errors": [],
  "duration_ms": 812.4,
  "metrics": {
    "api_cases": 3,
    "p95_latency_ms": 44.2
  }
}
```

Everything before that final JSON line is treated as logs.

## Decision Payload Back To Agent

The daemon returns compact evidence:

```json
{
  "run_id": "run_123",
  "status": "completed",
  "winner_candidate_id": "schema_first",
  "summary": "Schema First passed all focused API checks and was fastest among passing candidates.",
  "recommended_next_action": "Return evidence to coding agent",
  "available_actions": [
    {"action": "return_evidence", "label": "Return evidence to coding agent"},
    {"action": "apply_candidate", "label": "Offer apply winner", "candidate_id": "schema_first"}
  ],
  "candidates": [
    {
      "candidate_id": "schema_first",
      "label": "Schema First",
      "status": "passed",
      "exit_code": 0,
      "duration_ms": 812.4,
      "tests": {"passed": 3, "failed": 0, "total": 3},
      "metrics": {"p95_latency_ms": 44.2},
      "logs_url": "/runs/run_123/candidates/schema_first/logs",
      "code_url": "/runs/run_123/candidates/schema_first/code"
    }
  ]
}
```

For failed candidates, include focused failure details:

```json
{
  "candidate_id": "fast_path",
  "status": "failed",
  "tests": {"passed": 2, "failed": 1, "total": 3},
  "failures": [
    {
      "test": "invalid email",
      "details": "expected 400, got 201"
    }
  ]
}
```

This is the evidence the agent should use to revise, explain, or ask for a human decision.

## Future Implementation Plan

### Phase 1: App Fixture Support

Add fixture examples for a real app target:

- Node/Express or FastAPI sample app.
- `bench.json` with app runner command.
- Dockerfile with dependencies installed.
- Focused runner that probes one endpoint.
- Candidate files that replace one route/service module.

Acceptance:

- Four candidate route implementations run in isolated containers.
- Runner only probes the target endpoint.
- Decision Payload includes focused failures and API metrics.

### Phase 2: Source Snapshot Provider

Add a snapshot layer so the daemon can evaluate the current repo without manually copying it into `fixtures/`.

Responsibilities:

- Create a clean temp snapshot of the live workspace.
- Exclude ignored/build/cache directories.
- Preserve lockfiles and test files needed by the runner.
- Apply candidate file replacements to each candidate workspace.

Acceptance:

- Same request contract can target a real repo snapshot.
- Candidate workspaces are still isolated and comparable.

### Phase 3: Calling Surface Adapter

Build the first real orchestrator outside the daemon.

Likely first version:

- CLI command that accepts a target file plus candidate files.
- Later VS Code command that captures selected function/diff.
- Later MCP adapter that lets a Coding Agent call Bench directly.

Acceptance:

- Adapter submits dynamic candidates without hand-written request JSON.
- Adapter streams run events.
- Adapter returns the final Decision Payload to the user/agent.

### Phase 4: Patch-Based Candidates

Move beyond full-file replacement.

Add support for:

- Unified diffs.
- Multiple changed files.
- Validation that patches stay inside the snapshot.
- Detail endpoint showing exact applied diff/code.

Acceptance:

- Agent can propose realistic edits without replacing whole files.
- Backend still captures exact executed code.

### Phase 5: Apply Winner

Implement apply outside the daemon, likely in the Calling Surface.

Rules:

- Only offer apply when `available_actions` includes `apply_candidate`.
- Apply from exact candidate code/diff returned by detail endpoint.
- Keep a preview/diff before mutating the real workspace.
- Do not let the daemon silently mutate the real repo.

Acceptance:

- Human can inspect evidence, preview winner, then apply.
- Agent receives updated context after apply.

## Open Design Questions

- Should the first real app fixture be Node/Express, Next.js API routes, FastAPI, or the actual target project?
- Should live repo snapshots be created by the daemon or handed to the daemon by the Calling Surface?
- What is the first dynamic candidate source: CLI files, VS Code selection, MCP tool call, or chat card state?
- How should focused runners be authored: manually per fixture, generated by an agent, or selected from templates?
- What metrics are required for the first app demo: latency, memory, process startup time, endpoint correctness, screenshots, or logs only?

