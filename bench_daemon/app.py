from __future__ import annotations

import asyncio
import shutil
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel

from .executor import BenchOrchestrator, make_candidate_records
from .fixtures import FixtureError, list_fixtures, load_fixture, parse_candidate_request
from .models import TERMINAL_RUN_STATUSES
from .state import RunStore


class CandidateSpec(BaseModel):
    candidate_id: str
    label: str | None = None
    rationale: str | None = None
    files: dict[str, str]


class RunRequest(BaseModel):
    fixture_id: str = "python-merge"
    rebuild_image: bool = False
    candidates: list[CandidateSpec] | None = None


app = FastAPI(title="Bench Local Daemon", version="0.1.0")
store = RunStore()
orchestrator = BenchOrchestrator(store)


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "docker_available": shutil.which("docker") is not None,
    }


@app.get("/fixtures")
def fixtures() -> dict[str, Any]:
    try:
        return {"fixtures": [fixture.summary() for fixture in list_fixtures()]}
    except FixtureError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/runs")
async def create_run(body: RunRequest) -> dict[str, Any]:
    try:
        fixture = load_fixture(body.fixture_id)
        raw_candidates = (
            [_model_to_dict(candidate) for candidate in body.candidates]
            if body.candidates is not None
            else None
        )
        candidates = parse_candidate_request(fixture, raw_candidates)
    except FixtureError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    record = store.create_run(
        fixture.id,
        make_candidate_records(candidates, fixture),
    )
    asyncio.create_task(
        orchestrator.execute_run(record, fixture, candidates, body.rebuild_image)
    )
    return {
        "run_id": record.run_id,
        "status": record.status,
        "events_url": f"/runs/{record.run_id}/events",
        "result_url": f"/runs/{record.run_id}",
    }


@app.get("/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    record = store.get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return record.to_payload()


@app.get("/runs/{run_id}/events")
async def run_events(run_id: str, request: Request) -> StreamingResponse:
    record = store.get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Run not found")

    async def generate():
        queue = store.subscribe(record)
        last_sequence = 0
        try:
            replay = list(record.events)
            for event in replay:
                if await request.is_disconnected():
                    return
                last_sequence = event.sequence
                yield event.to_sse()

            while True:
                if record.status in TERMINAL_RUN_STATUSES and queue.empty():
                    return
                if await request.is_disconnected():
                    return
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                if event.sequence <= last_sequence:
                    continue
                last_sequence = event.sequence
                yield event.to_sse()
        finally:
            store.unsubscribe(record, queue)

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.get("/runs/{run_id}/candidates/{candidate_id}/logs")
def candidate_logs(run_id: str, candidate_id: str) -> PlainTextResponse:
    candidate = _get_candidate(run_id, candidate_id)
    return PlainTextResponse(candidate.logs)


@app.get("/runs/{run_id}/candidates/{candidate_id}/code")
def candidate_code(run_id: str, candidate_id: str) -> PlainTextResponse:
    candidate = _get_candidate(run_id, candidate_id)
    return PlainTextResponse(candidate.code)


@app.post("/maintenance/clear-local-runs")
def clear_local_runs() -> dict[str, Any]:
    return store.clear_local_runs()


def _get_candidate(run_id: str, candidate_id: str):
    record = store.get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Run not found")
    candidate = record.candidates.get(candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail="Candidate not found")
    return candidate


def _model_to_dict(model: BaseModel) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()
