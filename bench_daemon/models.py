from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


TERMINAL_RUN_STATUSES = {"completed", "failed"}
TERMINAL_CANDIDATE_STATUSES = {"passed", "failed", "timeout", "error"}
STATUS_RANK = {"passed": 0, "failed": 1, "timeout": 2, "error": 3}
RECOMMENDED_NEXT_ACTION = "Return evidence to coding agent"


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class RunEvent:
    sequence: int
    event: str
    data: dict[str, Any]
    timestamp: str = field(default_factory=utc_now)

    def to_sse(self) -> str:
        payload = dict(self.data)
        payload.setdefault("event", self.event)
        payload.setdefault("sequence", self.sequence)
        payload.setdefault("timestamp", self.timestamp)
        return f"event: {self.event}\ndata: {json.dumps(payload, sort_keys=True)}\n\n"


@dataclass
class CandidateRecord:
    candidate_id: str
    label: str
    rationale: str | None
    code: str
    files: dict[str, str] = field(default_factory=dict)
    status: str = "queued"
    exit_code: int | None = None
    duration_ms: float | None = None
    peak_memory_kb: float | None = None
    tests: dict[str, int] | None = None
    failures: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    logs: str = ""
    workspace_path: Path | None = None
    retained_workspace: bool = False

    def to_payload(self, run_id: str) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "candidate_id": self.candidate_id,
            "label": self.label,
            "status": self.status,
            "exit_code": self.exit_code,
            "duration_ms": self.duration_ms,
            "peak_memory_kb": self.peak_memory_kb,
            "tests": self.tests,
            "logs_url": f"/runs/{run_id}/candidates/{self.candidate_id}/logs",
            "code_url": f"/runs/{run_id}/candidates/{self.candidate_id}/code",
        }
        if self.rationale:
            payload["rationale"] = self.rationale
        if self.failures:
            payload["failures"] = self.failures
        if self.errors:
            payload["errors"] = self.errors
        if self.metrics:
            payload["metrics"] = self.metrics
        return payload

    def code_bundle(self) -> str:
        if not self.files:
            return self.code
        if len(self.files) == 1:
            return next(iter(self.files.values()))

        parts: list[str] = []
        for relative_path in sorted(self.files):
            contents = self.files[relative_path]
            parts.append(f"### {relative_path}\n{contents.rstrip()}\n")
        return "\n".join(parts)


@dataclass
class RunRecord:
    run_id: str
    fixture_id: str
    status: str = "queued"
    created_at: str = field(default_factory=utc_now)
    started_at: str | None = None
    completed_at: str | None = None
    winner_candidate_id: str | None = None
    summary: str | None = None
    recommended_next_action: str | None = None
    error: str | None = None
    candidate_order: list[str] = field(default_factory=list)
    candidates: dict[str, CandidateRecord] = field(default_factory=dict)
    ranked_candidate_ids: list[str] = field(default_factory=list)
    available_actions: list[dict[str, Any]] = field(default_factory=list)
    events: list[RunEvent] = field(default_factory=list)
    subscribers: set[Any] = field(default_factory=set)

    def set_candidates(self, candidates: list[CandidateRecord]) -> None:
        self.candidate_order = [candidate.candidate_id for candidate in candidates]
        self.candidates = {candidate.candidate_id: candidate for candidate in candidates}

    def sorted_candidates(self) -> list[CandidateRecord]:
        order = self.ranked_candidate_ids or self.candidate_order
        return [self.candidates[candidate_id] for candidate_id in order]

    def to_payload(self) -> dict[str, Any]:
        available_actions = self.available_actions
        if not available_actions and self.status == "completed":
            available_actions = build_available_actions(
                self.winner_candidate_id,
                self._winner_is_passing(),
            )

        payload: dict[str, Any] = {
            "run_id": self.run_id,
            "fixture_id": self.fixture_id,
            "status": self.status,
            "winner_candidate_id": self.winner_candidate_id,
            "summary": self.summary,
            "candidates": [
                candidate.to_payload(self.run_id) for candidate in self.sorted_candidates()
            ],
            "recommended_next_action": self.recommended_next_action,
            "available_actions": available_actions,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }
        if self.error:
            payload["error"] = self.error
        return payload

    def _winner_is_passing(self) -> bool:
        if not self.winner_candidate_id:
            return False
        winner = self.candidates.get(self.winner_candidate_id)
        return winner is not None and winner.status == "passed"


def rank_candidates(candidates: list[CandidateRecord]) -> list[CandidateRecord]:
    return sorted(
        candidates,
        key=lambda candidate: (
            STATUS_RANK.get(candidate.status, 99),
            candidate.duration_ms if candidate.duration_ms is not None else float("inf"),
            candidate.candidate_id,
        ),
    )


def build_summary(ranked: list[CandidateRecord]) -> tuple[str | None, str | None, str | None]:
    if not ranked:
        return None, "No candidates were available to rank.", "Review run failure"

    winner = ranked[0]
    if winner.status == "passed":
        summary = (
            f"{winner.label} passed all tests and was fastest among passing candidates."
        )
    elif winner.status == "failed":
        summary = f"No candidate passed; {winner.label} had the best failing result."
    elif winner.status == "timeout":
        summary = f"No candidate completed successfully; {winner.label} timed out."
    else:
        summary = f"No candidate completed successfully; {winner.label} errored."

    return winner.candidate_id, summary, RECOMMENDED_NEXT_ACTION


def build_available_actions(
    winner_candidate_id: str | None,
    can_apply_winner: bool = False,
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = [
        {
            "action": "return_evidence",
            "label": "Return evidence to coding agent",
        }
    ]
    if winner_candidate_id and can_apply_winner:
        actions.append(
            {
                "action": "apply_candidate",
                "label": "Offer apply winner",
                "candidate_id": winner_candidate_id,
            }
        )
    return actions
