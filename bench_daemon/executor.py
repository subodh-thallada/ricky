from __future__ import annotations

import asyncio
import json
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from .fixtures import CandidateInput, FixtureConfig
from .models import CandidateRecord, build_summary, rank_candidates
from .paths import LOCAL_RUNS_ROOT
from .state import RunStore


MAX_CONCURRENCY = 4


@dataclass(frozen=True)
class CommandResult:
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: float
    timed_out: bool = False


class BenchOrchestrator:
    def __init__(self, store: RunStore) -> None:
        self.store = store

    async def execute_run(
        self,
        record,
        fixture: FixtureConfig,
        candidates: list[CandidateInput],
        rebuild_image: bool,
    ) -> None:
        try:
            self.store.start_run(record)
            await self.store.emit(
                record,
                "run_started",
                {
                    "fixture_id": fixture.id,
                    "candidate_count": len(candidates),
                },
            )

            await self._ensure_image(record, fixture, rebuild_image)

            for candidate in candidates:
                await self.store.emit(
                    record,
                    "candidate_queued",
                    {
                        "candidate_id": candidate.candidate_id,
                        "status": "queued",
                    },
                )

            semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
            tasks = [
                asyncio.create_task(
                    self._run_candidate(record, fixture, candidate, semaphore)
                )
                for candidate in candidates
            ]
            await asyncio.gather(*tasks)

            ranked = rank_candidates(list(record.candidates.values()))
            winner_candidate_id, summary, action = build_summary(ranked)
            self.store.complete_run(
                record,
                [candidate.candidate_id for candidate in ranked],
                winner_candidate_id,
                summary,
                action,
            )
            await self.store.emit(
                record,
                "run_completed",
                {
                    "status": record.status,
                    "winner_candidate_id": winner_candidate_id,
                    "summary": summary,
                },
            )
        except Exception as exc:
            self.store.fail_run(record, str(exc))
            await self.store.emit(
                record,
                "run_failed",
                {
                    "status": "failed",
                    "error": str(exc),
                },
            )

    async def _ensure_image(
        self, record, fixture: FixtureConfig, rebuild_image: bool
    ) -> None:
        exists = False
        if not rebuild_image:
            inspect = await _run_command(
                ["docker", "image", "inspect", fixture.docker_image],
                timeout_seconds=30,
            )
            exists = inspect.exit_code == 0

        if exists:
            return

        await self.store.emit(
            record,
            "image_build_started",
            {
                "docker_image": fixture.docker_image,
            },
        )
        build = await _run_command(
            [
                "docker",
                "build",
                "-f",
                str(fixture.dockerfile_path),
                "-t",
                fixture.docker_image,
                str(fixture.docker_context_path),
            ],
            timeout_seconds=120,
        )
        if build.exit_code != 0:
            logs = _combine_logs(build.stdout, build.stderr)
            raise RuntimeError(f"Docker image build failed:\n{logs}")

        await self.store.emit(
            record,
            "image_build_finished",
            {
                "docker_image": fixture.docker_image,
                "duration_ms": build.duration_ms,
            },
        )

    async def _run_candidate(
        self,
        record,
        fixture: FixtureConfig,
        candidate: CandidateInput,
        semaphore: asyncio.Semaphore,
    ) -> None:
        async with semaphore:
            current = record.candidates[candidate.candidate_id]
            current.status = "running"
            await self.store.emit(
                record,
                "candidate_running",
                {
                    "candidate_id": candidate.candidate_id,
                    "status": "running",
                },
            )

            workspace = _create_workspace(record.run_id, fixture, candidate)
            current.workspace_path = workspace
            current.code = candidate.files[fixture.target_file]

            command = [
                "docker",
                "run",
                "--rm",
                "--name",
                _container_name(record.run_id, candidate.candidate_id),
                "--network",
                "none",
                "--memory",
                "256m",
                "--cpus",
                "1.0",
                "--pids-limit",
                "128",
                "-v",
                f"{workspace}:/work:ro",
                "-w",
                "/work",
                fixture.docker_image,
                "sh",
                "-lc",
                fixture.runner,
            ]

            result = await _run_command(
                command,
                timeout_seconds=fixture.timeout_ms / 1000,
                container_name=_container_name(record.run_id, candidate.candidate_id),
            )
            _apply_command_result(current, result)

            if current.status == "passed":
                shutil.rmtree(workspace, ignore_errors=True)
                current.retained_workspace = False
            else:
                current.retained_workspace = workspace.exists()

            await self.store.emit(
                record,
                "candidate_finished",
                {
                    "candidate_id": candidate.candidate_id,
                    "status": current.status,
                    "exit_code": current.exit_code,
                    "duration_ms": current.duration_ms,
                    "tests": current.tests,
                },
            )


def make_candidate_records(candidates: list[CandidateInput], fixture: FixtureConfig) -> list[CandidateRecord]:
    return [
        CandidateRecord(
            candidate_id=candidate.candidate_id,
            label=candidate.label,
            rationale=candidate.rationale,
            code=candidate.files[fixture.target_file],
        )
        for candidate in candidates
    ]


def parse_runner_output(result: CommandResult) -> tuple[str, dict[str, object] | None, str]:
    stdout_lines = result.stdout.splitlines()
    if not stdout_lines:
        return _combine_logs(result.stdout, result.stderr), None, "runner produced no stdout"

    final_line = stdout_lines[-1]
    log_parts = stdout_lines[:-1]
    logs = "\n".join(log_parts)
    if logs:
        logs += "\n"
    logs = _combine_logs(logs, result.stderr)

    try:
        parsed = json.loads(final_line)
    except json.JSONDecodeError as exc:
        return _combine_logs(result.stdout, result.stderr), None, f"invalid final JSON line: {exc}"

    if not isinstance(parsed, dict):
        return logs, None, "final JSON line was not an object"
    return logs, parsed, ""


def _apply_command_result(candidate: CandidateRecord, result: CommandResult) -> None:
    candidate.exit_code = result.exit_code
    if result.timed_out:
        candidate.status = "timeout"
        candidate.duration_ms = result.duration_ms
        candidate.logs = _combine_logs(result.stdout, result.stderr)
        candidate.tests = {"passed": 0, "failed": 1, "total": 1}
        return

    logs, parsed, parse_error = parse_runner_output(result)
    candidate.logs = logs

    if parsed is None:
        candidate.status = "error"
        candidate.duration_ms = result.duration_ms
        candidate.logs = _combine_logs(logs, parse_error)
        candidate.tests = {"passed": 0, "failed": 1, "total": 1}
        return

    tests = parsed.get("tests")
    duration_ms = parsed.get("duration_ms")
    candidate.tests = tests if _is_tests_summary(tests) else {"passed": 0, "failed": 1, "total": 1}
    candidate.duration_ms = (
        float(duration_ms)
        if isinstance(duration_ms, int | float)
        else result.duration_ms
    )

    if result.exit_code == 0 and candidate.tests.get("failed", 1) == 0:
        candidate.status = "passed"
    else:
        candidate.status = "failed"


async def _run_command(
    args: list[str],
    timeout_seconds: float | None,
    container_name: str | None = None,
) -> CommandResult:
    started = time.perf_counter()
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"Command not found: {args[0]}") from exc

    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=timeout_seconds
        )
        duration_ms = round((time.perf_counter() - started) * 1000, 3)
        return CommandResult(
            exit_code=proc.returncode,
            stdout=stdout_bytes.decode("utf-8", errors="replace"),
            stderr=stderr_bytes.decode("utf-8", errors="replace"),
            duration_ms=duration_ms,
        )
    except asyncio.TimeoutError:
        proc.kill()
        if container_name:
            await _remove_container(container_name)
        stdout_bytes, stderr_bytes = await proc.communicate()
        duration_ms = round((time.perf_counter() - started) * 1000, 3)
        return CommandResult(
            exit_code=-1,
            stdout=stdout_bytes.decode("utf-8", errors="replace"),
            stderr=stderr_bytes.decode("utf-8", errors="replace"),
            duration_ms=duration_ms,
            timed_out=True,
        )


async def _remove_container(container_name: str) -> None:
    proc = await asyncio.create_subprocess_exec(
        "docker",
        "rm",
        "-f",
        container_name,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.communicate()


def _create_workspace(
    run_id: str, fixture: FixtureConfig, candidate: CandidateInput
) -> Path:
    workspace = LOCAL_RUNS_ROOT / run_id / candidate.candidate_id
    if workspace.exists():
        shutil.rmtree(workspace)

    shutil.copytree(
        fixture.root,
        workspace,
        ignore=shutil.ignore_patterns(
            fixture.candidates_dir,
            "__pycache__",
            "*.pyc",
        ),
    )

    for relative_path, contents in candidate.files.items():
        target = _resolve_workspace_path(workspace, relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(contents, encoding="utf-8")

    return workspace


def _resolve_workspace_path(workspace: Path, relative_path: str) -> Path:
    root = workspace.resolve()
    target = (root / relative_path).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise RuntimeError(f"Candidate file escapes workspace: {relative_path}") from exc
    return target


def _container_name(run_id: str, candidate_id: str) -> str:
    return f"bench-{run_id}-{candidate_id}"[:120]


def _combine_logs(*parts: str) -> str:
    return "".join(part for part in parts if part)


def _is_tests_summary(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    return all(isinstance(value.get(key), int) for key in ("passed", "failed", "total"))
