from __future__ import annotations

import asyncio
import contextlib
import json
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from .fixtures import CandidateInput, FixtureConfig
from .models import CandidateRecord, build_available_actions, build_summary, rank_candidates
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
    peak_container_memory_kb: float | None = None


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
            can_apply_winner = bool(ranked and ranked[0].status == "passed")
            available_actions = build_available_actions(
                winner_candidate_id,
                can_apply_winner,
            )
            self.store.complete_run(
                record,
                [candidate.candidate_id for candidate in ranked],
                winner_candidate_id,
                summary,
                action,
                available_actions,
            )
            await self.store.emit(
                record,
                "run_completed",
                {
                    "status": record.status,
                    "winner_candidate_id": winner_candidate_id,
                    "summary": summary,
                    "recommended_next_action": action,
                    "available_actions": available_actions,
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
            await self.store.emit(
                record,
                "image_build_skipped",
                {
                    "docker_image": fixture.docker_image,
                    "reason": "image_exists",
                },
            )
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

            workspace = create_candidate_workspace(record.run_id, fixture, candidate)
            current.workspace_path = workspace
            current.code = candidate.files[fixture.target_file]
            current.files = dict(candidate.files)

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
            apply_command_result(current, result)

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
                    "peak_memory_kb": current.peak_memory_kb,
                    "tests": current.tests,
                    "failures": current.failures,
                    "errors": current.errors,
                    "metrics": current.metrics,
                },
            )


def make_candidate_records(candidates: list[CandidateInput], fixture: FixtureConfig) -> list[CandidateRecord]:
    return [
        CandidateRecord(
            candidate_id=candidate.candidate_id,
            label=candidate.label,
            rationale=candidate.rationale,
            code=candidate.files[fixture.target_file],
            files=dict(candidate.files),
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


def apply_command_result(candidate: CandidateRecord, result: CommandResult) -> None:
    candidate.exit_code = result.exit_code
    if result.timed_out:
        candidate.status = "timeout"
        candidate.duration_ms = result.duration_ms
        candidate.peak_memory_kb = result.peak_container_memory_kb
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
    candidate.failures = _list_of_dicts(parsed.get("failures"))
    candidate.errors = _list_of_dicts(parsed.get("errors"))
    candidate.metrics = _extract_metrics(parsed)
    candidate.peak_memory_kb = _resolve_peak_memory_kb(parsed, result)
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
    peak_container_kb: float | None = None
    poll_task: asyncio.Task | None = None

    async def poll_container_stats() -> None:
        nonlocal peak_container_kb
        assert container_name is not None
        while True:
            await asyncio.sleep(0.15)
            proc = await asyncio.create_subprocess_exec(
                "docker",
                "stats",
                container_name,
                "--no-stream",
                "--format",
                "{{.MemUsage}}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout_bytes, _ = await proc.communicate()
            if proc.returncode != 0:
                continue
            usage_kb = _parse_docker_mem_usage(
                stdout_bytes.decode("utf-8", errors="replace").strip()
            )
            if usage_kb is None:
                continue
            peak_container_kb = (
                usage_kb
                if peak_container_kb is None
                else max(peak_container_kb, usage_kb)
            )

    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"Command not found: {args[0]}") from exc

    if container_name:
        poll_task = asyncio.create_task(poll_container_stats())

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
            peak_container_memory_kb=peak_container_kb,
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
            peak_container_memory_kb=peak_container_kb,
        )
    finally:
        if poll_task is not None:
            poll_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await poll_task


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


def create_candidate_workspace(
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


def _list_of_dicts(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _extract_metrics(parsed: dict[str, object]) -> dict[str, object]:
    metrics: dict[str, object] = {}
    for key in ("metrics", "benchmark_metrics", "benchmarks"):
        value = parsed.get(key)
        if isinstance(value, dict):
            metrics.update(value)
    return metrics


def _extract_peak_memory_kb(parsed: dict[str, object]) -> float | None:
    value = parsed.get("peak_memory_kb")
    if isinstance(value, int | float):
        return float(value)
    metrics = _extract_metrics(parsed)
    nested = metrics.get("peak_memory_kb")
    if isinstance(nested, int | float):
        return float(nested)
    return None


def _resolve_peak_memory_kb(
    parsed: dict[str, object], result: CommandResult
) -> float | None:
    runner_peak = _extract_peak_memory_kb(parsed)
    container_peak = result.peak_container_memory_kb
    if runner_peak is not None and container_peak is not None:
        return max(runner_peak, container_peak)
    if runner_peak is not None:
        return runner_peak
    return container_peak


def _parse_docker_mem_usage(raw: str) -> float | None:
    if not raw:
        return None
    usage_part = raw.split("/", 1)[0].strip()
    match = re.match(r"^([\d.]+)\s*(B|KiB|MiB|GiB|KB|MB|GB)?$", usage_part)
    if not match:
        return None
    amount = float(match.group(1))
    unit = match.group(2) or "B"
    multipliers = {
        "B": 1 / 1024,
        "KiB": 1,
        "KB": 1,
        "MiB": 1024,
        "MB": 1024,
        "GiB": 1024 * 1024,
        "GB": 1024 * 1024,
    }
    multiplier = multipliers.get(unit)
    if multiplier is None:
        return None
    return round(amount * multiplier, 3)


_apply_command_result = apply_command_result
_create_workspace = create_candidate_workspace
