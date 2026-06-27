<div align="center">

# ⚡ BENCH

### The **trust-but-verify** layer for agentic coding — live inside VS Code.

*Your agent gives you one answer and says "trust me."*
**Bench gives you four — measured, sandboxed, ranked — in the time your agent wrote one.**

<br/>

`VS Code Extension` · `FastAPI Orchestrator` · `Docker Sandbox Daemon` · `Cerebras` · `Claude` · `Backboard`

![Status](https://img.shields.io/badge/status-hackathon%20v1%20·%20building%20live-ff7a17?style=for-the-badge)
![Language](https://img.shields.io/badge/python-3.11+-7c3aed?style=for-the-badge)
![Surface](https://img.shields.io/badge/surface-VS%20Code-a0c3ec?style=for-the-badge)
![Evidence](https://img.shields.io/badge/evidence-real%20Docker%20runs-191919?style=for-the-badge)

> **Last updated:** 2026-06-27 · **This is a living build** — sections marked 🚧 are mid-flight.

</div>

---

## 🎯 The one-liner judges remember

> **"Your AI agent gives you options. Bench lets you test *all of them*, side-by-side, inside VS Code — in the time the agent took to write one. That's only possible because Cerebras runs a whole tournament while other models run a single suggestion."**

---

## 🔥 The problem (everyone in the room has felt this)

You're shipping with an AI coding agent — Claude Code, Cursor, Copilot, whatever. It writes the function. Or it offers *"three ways we could do this."* Either way it hands you options and says **trust me**:

- ❌ No evidence
- ❌ No measured alternatives
- ❌ No memory of what *you* actually prefer

You either **accept on faith** (and get scared at merge time) or **burn an afternoon** checking it yourself across three terminal tabs.

**Today's tools made writing code fast. Nobody made *choosing* code trustworthy.** That's the half Bench owns.

---

## 💡 The product

**Bench is the sidecar for the "trust me" moment.** It lives as a VS Code side panel:

```
┌────────────────────────┬───────────────────────────────┐
│                        │  💬 Bench Chat                 │
│                        │  ───────────────────────────   │
│      your code         │  ┌─ Readable ──┐ ✅ 12/12      │
│      (editor)          │  │ Fast        │ ✅ 12/12  18× │
│                        │  │ Low memory  │ ✅ 12/12      │
│                        │  │ Clever      │ ❌ fails []   │
│                        │  └─────────────┘               │
│                        │  [ Test all ]   [ Apply winner]│
│                        │  ▸ sandbox peek rail (logs/diff)│
└────────────────────────┴───────────────────────────────┘
```

In **~4 seconds** it gives you the **best 4 options, measured** — the agent's own version *plus* genuinely-different alternatives — each run in an **isolated Docker sandbox** with real numbers: runtime, memory, LOC, deps, which edge cases pass. **You pick. Bench learns your taste. Bench can apply the winner.**

It doesn't replace your agent. **It's the verify half today's tools skip.**

---

## 🎬 The 90-second demo moment

1. You build a feature in **VS Code**. The agent proposes approaches for `merge_intervals`.
2. Bench renders them as **structured approach cards**: `Readable` · `Fast` · `Low memory` — each with rationale, expected tradeoff, and a `Test` control.
3. You hit **Test all**. Bench fires each into its own background Docker sandbox and streams status into a slim **peek rail**.
4. In **~4 seconds** the cards resolve with measured evidence: green/red badges, runtime on 10k inputs, peak memory, LOC, deps, failure details.
5. The spread tells the story: readable is clean + correct, brute-force is **18× slower**, the heap one is heavier for no gain, and the clever stack version **fails on `[]`** — with the exact input shown.
6. You click **Apply winner**. Bench returns a structured **Decision Payload**, and remembers you lead with readability next time.

---

## 🏆 Why it wins

| Principle | What it means |
|---|---|
| **The developer decides** | No single-right-answer theater. Show the real option space with evidence — the way a senior reasons and a junior learns. |
| **Honesty over confidence** | Every metric is a **measured run in a sandbox**. No claim ships without a number. Purpose-built to kill the AI overconfidence that makes you afraid to merge. |
| **Stay in the editor** | Chat, options, runs, diffs, apply — all in the VS Code side panel. No separate dashboard. |
| **The taste to stay quiet** | One-liner / glue code? No tournament. Knowing *when not to fire* is the tasteful part. |
| **Complement, don't compete** | Agent-agnostic plumbing. **The supplied implementation is always one of the four options** — Bench measures it, never silently overrides it. |

---

## 🧩 Sponsor tools are load-bearing (not bolted on)

| Tool | Role | Remove it and… |
|---|---|---|
| ⚡ **Cerebras** | The reason it can *exist*. Generating alternatives + reasoning *inside the dev's flow* at **~1,800–2,000+ tok/s in parallel** turns minutes into seconds — so Bench can fire on every meaningful function. **Speed makes a new kind of tool usable.** | …the loop is too slow to ever fire. Back to one "trust me." |
| 🐳 **Docker** | The evidence. You can't say "the heap variant beats Claude's by 2×, the clever one fails on `[]`" unless you actually run them, isolated and measured. N ephemeral sandboxes are the proof. | …you're back to vibes / hallucinated benchmarks. |
| 🧠 **Backboard** | Your team's taste, persisted (`moonshotai/kimi-k2.6`). Learns picks across repos ("we prefer readable over clever; we avoid new deps") and pre-ranks output to it. | …no learning, no judgment handoff. Juniors don't inherit team taste. |
| 🤖 **Claude (Anthropic)** | The conversational + structuring brain (`claude-haiku-4-5`). Runs the in-editor chat and reshapes raw generations into clean, comparable option cards — **code stays Cerebras's; Claude only makes it legible.** | …cards are messy, chat is dumb. |

---

## 🏗️ Architecture (as built)

Three processes, all local, talking over HTTP:

```mermaid
flowchart LR
    subgraph editor["🖥️ VS Code Extension (TypeScript)"]
      CHAT["Side-panel chat<br/>+ approach cards"]
      PEEK["Sandbox peek rail<br/>logs / diff / metrics"]
      RUNNER["daemonSandboxRunner"]
    end

    subgraph orch["⚙️ Orchestrator — FastAPI :8000"]
      ROUTER["chat_router<br/>mode + provider routing"]
      OPTS["feature_options<br/>generate + structure"]
      CTX["repo_context / context_inference"]
      THREADS["thread_store / thread_chat"]
    end

    subgraph daemon["🐳 Bench Daemon — FastAPI :8001"]
      EXEC["BenchOrchestrator<br/>asyncio · sem(4)"]
      DOCK["Docker build / run<br/>per-candidate sandbox"]
      RANK["rank + Decision Payload"]
    end

    CHAT --> ROUTER
    ROUTER --> OPTS
    OPTS -->|Cerebras gen + Claude structure| CHAT
    CHAT -->|Test all| RUNNER
    RUNNER -->|POST /runs| EXEC
    EXEC --> DOCK --> RANK
    RANK -->|SSE /runs/{id}/events| PEEK

    OPTS -. taste .-> BB[("🧠 Backboard")]
```

### The provider split (the secret sauce)

```
┌──────────────┬───────────────────────────────┬──────────────────────────────┐
│   Provider   │   Model                        │   Job                         │
├──────────────┼───────────────────────────────┼──────────────────────────────┤
│ ⚡ Cerebras   │ zai-glm-4.7 (reasoning)        │ PARALLEL code generation of   │
│              │                                │ the alternatives — the speed  │
│ 🤖 Anthropic │ claude-haiku-4-5-20251001      │ in-editor chat + restructure  │
│              │                                │ raw output into option cards  │
│ 💎 Gemini    │ gemini-2.5-flash               │ context/plan (available)      │
│ 🧠 Backboard │ moonshotai/kimi-k2.6 (OpenRtr) │ persistent per-repo taste     │
└──────────────┴───────────────────────────────┴──────────────────────────────┘
```

> Keeping **chat + generation** (orchestrator) separate from **sandbox execution** (daemon) is the design: the thing that *talks* never touches the thing that *measures*.

---

## 🔌 The Daemon API (real, running)

FastAPI on `127.0.0.1:8001`, streaming over **Server-Sent Events**:

| Method | Route | Purpose |
|---|---|---|
| `GET`  | `/health` | liveness for extension auto-start |
| `GET`  | `/fixtures` | list runnable targets |
| `POST` | `/runs` | submit a Candidate Evaluation Request |
| `GET`  | `/runs/{id}` | full run + Decision Payload |
| `GET`  | `/runs/{id}/events` | **SSE** live status (15s keepalive) |
| `GET`  | `/runs/{id}/candidates/{cid}/logs` | raw container logs |
| `GET`  | `/runs/{id}/candidates/{cid}/code` | generated code bundle |
| `POST` | `/maintenance/clear-local-runs` | wipe local run state |

**Execution model:** `asyncio.Semaphore(MAX_CONCURRENCY=4)` → up to 4 candidates run concurrently, each `docker build` (cached via `docker image inspect`) → `docker run` with per-fixture `timeout_ms`. Results ranked: `passed < failed < timeout < error`, ties broken by duration. Winner + summary + `available_actions` (`return_evidence`, `apply_candidate`) become the **Decision Payload**.

---

## 🗣️ The language (so the team + judges speak one dialect)

| Term | Meaning |
|---|---|
| **Candidate** | A proposed implementation Bench can measure against others. |
| **Candidate Run** | One isolated measurement of a single Candidate. |
| **Test Fixture** | A small local codebase with deterministic tests — the first run target. |
| **Calling Surface** | The UI/adapter that starts a run and receives the result. |
| **Decision Payload** | The structured, ranked result returned to the Calling Surface. |

---

## 📂 Repo map

```
new-hack26/
├── src/                         🖥️  VS Code extension (TypeScript)
│   ├── extension.ts             #   activation, chat webview, commands
│   ├── types.ts
│   └── services/
│       └── daemonSandboxRunner.ts  #  drives Test all → daemon, reads SSE
├── bench/                       ⚙️  Orchestrator (FastAPI :8000)
│   ├── main.py · config.py · schemas.py
│   ├── clients/                 #   anthropic · cerebras · gemini · backboard
│   └── services/                #   chat_router · feature_options · repo_context …
├── bench_daemon/                🐳  Sandbox daemon (FastAPI :8001)
│   ├── app.py · executor.py     #   Docker build/run, SSE, ranking
│   ├── fixtures.py · models.py · state.py
├── fixtures/                    🧪  run targets (python-merge, fastapi-auth, mock-shop*)
├── bench-demo-workspace/        🎥  live demo workspace
├── bench-product-spec.md        📜  full product spec
├── DESIGN.md                    🎨  xAI-inspired design language (dark canvas, pill UI)
└── HACKATHON.md                 ⚡  ← you are here
```

---

## 🎨 Design language

Bench wears an **xAI-inspired** skin (see `DESIGN.md`): a strict near-black canvas (`#0a0a0a`), white outline **pill** as the entire interactive vocabulary, `Universal Sans` weight-400 display with tight negative tracking, and `Geist Mono` uppercase eyebrows that read like code comments. Engineered, cosmic, unmarketed. **Hairlines carry elevation — no drop shadows.** A muted sunset-orange/dusk-purple accent set appears only on product moments.

---

## ✅ Status — what's live vs 🚧 in-flight

**Working now**
- ✅ VS Code side-panel chat + suggestion cards + details panel (code / plan / tradeoffs / metrics)
- ✅ **Cerebras** parallel generation of alternatives (`zai-glm-4.7`)
- ✅ **Claude Haiku 4.5** in-editor chat + card restructuring
- ✅ **Docker daemon**: real isolated runs, SSE streaming, ranking, Decision Payload
- ✅ Fixtures: `python-merge`, `fastapi-auth-endpoint`, + `mock-shop` family
- ✅ Candidate logs view + sandbox peek

**🚧 In-flight / day-2**
- 🚧 **Apply winner** patches the real workspace (today: marks selected + returns evidence)
- 🚧 **Backboard** taste vector visibly re-ranks on run #2
- 🚧 Agent adapters (Claude Code / Cursor / Pi / Aider / Copilot)
- 🚧 Multi-language (TS / Go) — v1 is **Python, function-level**
- 🚧 Deeper container security hardening

---

## 🚀 Run it

**1 · Orchestrator** (`:8000`)
```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .
cp .env.example .env        # add CEREBRAS_API_KEY + ANTHROPIC_API_KEY
uvicorn bench.main:app --reload --host 127.0.0.1 --port 8000
python -m bench.scripts.check_providers
```

**2 · Daemon** (`:8001`, needs Docker Desktop)
```bash
python3 -m bench_daemon serve --host 127.0.0.1 --port 8001
# verify the Docker path independently:
python3 -m bench_daemon run --base-url http://127.0.0.1:8001 --fixture-id python-merge
```

**3 · Extension**
```bash
npm install && npm run compile
# F5 in VS Code → "Run Bench Extension" → open Bench from the Activity Bar
```

---

<div align="center">

### ⚡ Bench doesn't out-write your agent. It out-*decides* for you.

**Generate fast (Cerebras). Prove it (Docker). Remember your taste (Backboard). Decide in VS Code (Claude).**

*The verify half — finally built.*

</div>
