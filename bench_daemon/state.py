from __future__ import annotations

import asyncio
import shutil
import uuid
from pathlib import Path
from typing import Any

from .models import CandidateRecord, RunEvent, RunRecord, utc_now
from .paths import LOCAL_RUNS_ROOT


class RunStore:
    def __init__(self) -> None:
        self._runs: dict[str, RunRecord] = {}

    def create_run(self, fixture_id: str, candidates: list[CandidateRecord]) -> RunRecord:
        run_id = f"run_{uuid.uuid4().hex[:12]}"
        record = RunRecord(run_id=run_id, fixture_id=fixture_id)
        record.set_candidates(candidates)
        self._runs[run_id] = record
        return record

    def get(self, run_id: str) -> RunRecord | None:
        return self._runs.get(run_id)

    async def emit(self, record: RunRecord, event: str, data: dict[str, Any]) -> None:
        event_data = {"run_id": record.run_id, **data}
        item = RunEvent(
            sequence=len(record.events) + 1,
            event=event,
            data=event_data,
        )
        record.events.append(item)
        for queue in tuple(record.subscribers):
            queue.put_nowait(item)

    def subscribe(self, record: RunRecord) -> asyncio.Queue[RunEvent]:
        queue: asyncio.Queue[RunEvent] = asyncio.Queue()
        record.subscribers.add(queue)
        return queue

    def unsubscribe(self, record: RunRecord, queue: asyncio.Queue[RunEvent]) -> None:
        record.subscribers.discard(queue)

    def start_run(self, record: RunRecord) -> None:
        record.status = "running"
        record.started_at = utc_now()

    def complete_run(
        self,
        record: RunRecord,
        ranked_candidate_ids: list[str],
        winner_candidate_id: str | None,
        summary: str | None,
        recommended_next_action: str | None,
    ) -> None:
        record.status = "completed"
        record.completed_at = utc_now()
        record.ranked_candidate_ids = ranked_candidate_ids
        record.winner_candidate_id = winner_candidate_id
        record.summary = summary
        record.recommended_next_action = recommended_next_action

    def fail_run(self, record: RunRecord, error: str) -> None:
        record.status = "failed"
        record.completed_at = utc_now()
        record.error = error
        record.summary = error
        record.recommended_next_action = "Inspect run failure"

    def active_workspace_paths(self) -> set[Path]:
        active: set[Path] = set()
        for record in self._runs.values():
            for candidate in record.candidates.values():
                if candidate.status == "running" and candidate.workspace_path:
                    active.add(candidate.workspace_path.resolve())
        return active

    def clear_local_runs(self) -> dict[str, Any]:
        active = self.active_workspace_paths()
        removed: list[str] = []

        if LOCAL_RUNS_ROOT.exists():
            for run_dir in LOCAL_RUNS_ROOT.iterdir():
                if not run_dir.is_dir():
                    continue
                for candidate_dir in run_dir.iterdir():
                    if not candidate_dir.is_dir():
                        continue
                    resolved = candidate_dir.resolve()
                    if resolved in active:
                        continue
                    shutil.rmtree(candidate_dir, ignore_errors=True)
                    removed.append(str(candidate_dir))
                try:
                    run_dir.rmdir()
                except OSError:
                    pass

        for record in self._runs.values():
            for candidate in record.candidates.values():
                if candidate.workspace_path and not candidate.workspace_path.exists():
                    candidate.retained_workspace = False

        return {"removed_count": len(removed), "removed": removed}
