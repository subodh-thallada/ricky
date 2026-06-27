# Bench Context

Bench helps developers compare agent-proposed code approaches by running each candidate locally and showing measured evidence inside VS Code.

## Language

**Candidate**:
A proposed implementation that Bench can measure against other proposed implementations.
_Avoid_: Option, approach, app

**Candidate Run**:
One isolated measurement of a single **Candidate** against a known local target.
_Avoid_: App build, background instance

**Test Fixture**:
A small local codebase with deterministic tests used as the first target for **Candidate Runs**.
_Avoid_: Dummy app, sample repo

**App Fixture**:
A small runnable local app used after the **Test Fixture** phase to demonstrate build or preview behavior.
_Avoid_: Production app, full app instance

**Calling Surface**:
The UI or adapter that starts a Bench run and receives the result.
_Avoid_: LLM, agent, chat when the specific surface is not known

**Coding Agent**:
An external tool that proposes one or more **Candidates** and can receive measured evidence from Bench.
_Avoid_: Bench, Calling Surface

**Candidate Evaluation Request**:
A structured request containing **Candidates** to run, test, benchmark, and summarize.
_Avoid_: Goal command, UI click, auto-apply

**Decision Payload**:
The structured result returned to the **Calling Surface** after Bench ranks the candidates.
_Avoid_: LLM response, chat message

## Relationships

- A **Candidate Run** measures exactly one **Candidate**.
- A **Test Fixture** is the first supported target for **Candidate Runs**.
- An **App Fixture** follows the **Test Fixture** once the local sandbox loop is working.
- A **Calling Surface** starts Bench work and receives one **Decision Payload**.
- A **Coding Agent** can supply **Candidates** through a **Candidate Evaluation Request**.
- A **Candidate Evaluation Request** produces one **Candidate Run** per **Candidate**.
- A **Decision Payload** summarizes the **Candidate Runs** for one comparison.

## Example Dialogue

> **Dev:** "Should Bench build the whole app for every proposed approach, and where does the result go?"
> **Domain expert:** "Not for the MVP. Start with a **Test Fixture** so each **Candidate Run** can apply one candidate and run deterministic tests. Return the **Decision Payload** to the **Calling Surface**. Move to an **App Fixture** once that loop is stable."

## Flagged Ambiguities

- "build those apps" was used to describe local sandbox work. Resolved: the MVP runs **Candidate Runs** against a **Test Fixture**, then transitions to an **App Fixture**.
- "return back to the LLM" was used to describe result delivery. Resolved: the MVP returns a **Decision Payload** to the **Calling Surface** and does not inject into Cursor or Claude Code.
- "goal command" was used to describe the agent implementation loop. Resolved: this means a **Candidate Evaluation Request** that runs supplied **Candidates** in containers and returns measured evidence, not a UI command or automatic workspace apply.
