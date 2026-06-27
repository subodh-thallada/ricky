import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bench_daemon.executor import BenchOrchestrator, CommandResult, make_candidate_records
from bench_daemon.fixtures import CandidateInput, load_fixture
from bench_daemon.state import RunStore


class OrchestratorTests(unittest.IsolatedAsyncioTestCase):
    async def test_docker_setup_error_fails_run_before_candidate_execution(self):
        fixture = load_fixture("python-merge")
        store = RunStore()
        candidates = [
            CandidateInput(
                candidate_id="candidate",
                label="Candidate",
                rationale=None,
                files={
                    fixture.target_file: "def merge_intervals(intervals):\n    return intervals\n",
                },
            )
        ]
        record = store.create_run(
            fixture.id,
            make_candidate_records(candidates, fixture),
        )
        orchestrator = BenchOrchestrator(store)

        async def docker_build_fails(args, timeout_seconds, container_name=None):
            if args[:3] == ["docker", "image", "inspect"]:
                return CommandResult(1, "", "missing", 1)
            if args[:2] == ["docker", "build"]:
                return CommandResult(1, "", "build failed", 2)
            raise AssertionError(f"Unexpected command: {args}")

        with patch("bench_daemon.executor._run_command", docker_build_fails):
            await orchestrator.execute_run(
                record,
                fixture,
                candidates,
                rebuild_image=False,
            )

        self.assertEqual(record.status, "failed")
        self.assertIn("Docker image build failed", record.error)
        self.assertEqual(record.candidates["candidate"].status, "queued")
        self.assertEqual(record.events[-1].event, "run_failed")

    async def test_run_isolates_candidate_failures_ranks_results_and_cleans_up(self):
        fixture = load_fixture("python-merge")
        store = RunStore()
        candidates = [
            CandidateInput(
                candidate_id="bad",
                label="Bad",
                rationale=None,
                files={
                    fixture.target_file: "def merge_intervals(intervals):\n    return []\n",
                },
            ),
            CandidateInput(
                candidate_id="good",
                label="Good",
                rationale=None,
                files={
                    fixture.target_file: "def merge_intervals(intervals):\n    return intervals\n",
                },
            ),
        ]
        record = store.create_run(
            fixture.id,
            make_candidate_records(candidates, fixture),
        )
        orchestrator = BenchOrchestrator(store)

        async def fake_docker(args, timeout_seconds, container_name=None):
            if args[:3] == ["docker", "image", "inspect"]:
                return CommandResult(0, "", "", 1)
            if args[:2] == ["docker", "run"]:
                workspace = _workspace_from_docker_args(args)
                code = (workspace / fixture.target_file).read_text(encoding="utf-8")
                if "return []" in code:
                    return CommandResult(
                        1,
                        (
                            "failure log\n"
                            '{"duration_ms": 2.5, "tests": {"passed": 7, "failed": 1, "total": 8}, '
                            '"failures": [{"test": "test_empty_input", "details": "expected []"}]}\n'
                        ),
                        "",
                        3,
                    )
                return CommandResult(
                    0,
                    '{"duration_ms": 4.5, "tests": {"passed": 8, "failed": 0, "total": 8}, '
                    '"metrics": {"runtime_p95_ms": 4.4}}\n',
                    "",
                    5,
                )
            raise AssertionError(f"Unexpected command: {args}")

        with tempfile.TemporaryDirectory() as tmpdir:
            runs_root = Path(tmpdir)
            with patch("bench_daemon.executor.LOCAL_RUNS_ROOT", runs_root):
                with patch("bench_daemon.state.LOCAL_RUNS_ROOT", runs_root):
                    with patch("bench_daemon.executor._run_command", fake_docker):
                        await orchestrator.execute_run(
                            record,
                            fixture,
                            candidates,
                            rebuild_image=False,
                        )

                    payload = record.to_payload()
                    self.assertEqual(payload["status"], "completed")
                    self.assertEqual(payload["winner_candidate_id"], "good")
                    self.assertEqual(
                        [candidate["candidate_id"] for candidate in payload["candidates"]],
                        ["good", "bad"],
                    )
                    self.assertEqual(payload["candidates"][0]["status"], "passed")
                    self.assertEqual(payload["candidates"][0]["metrics"]["runtime_p95_ms"], 4.4)
                    self.assertEqual(payload["candidates"][1]["status"], "failed")
                    self.assertEqual(payload["candidates"][1]["failures"][0]["test"], "test_empty_input")
                    self.assertFalse(record.candidates["good"].workspace_path.exists())
                    self.assertTrue(record.candidates["bad"].workspace_path.exists())

                    event_names = [event.event for event in record.events]
                    self.assertIn("image_build_skipped", event_names)
                    self.assertEqual(event_names.count("candidate_finished"), 2)
                    self.assertEqual(event_names[-1], "run_completed")

                    cleanup = store.clear_local_runs()
                    self.assertGreaterEqual(cleanup["removed_count"], 1)
                    self.assertFalse(record.candidates["bad"].workspace_path.exists())


def _workspace_from_docker_args(args):
    volume_arg = args[args.index("-v") + 1]
    return Path(volume_arg.split(":", 1)[0])


if __name__ == "__main__":
    unittest.main()
