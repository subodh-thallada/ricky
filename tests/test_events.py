import unittest
from unittest.mock import patch

from bench_daemon.executor import BenchOrchestrator, CommandResult
from bench_daemon.fixtures import load_fixture
from bench_daemon.state import RunStore


class EventTests(unittest.IsolatedAsyncioTestCase):
    async def test_emits_image_build_skipped_when_fixture_image_exists(self):
        store = RunStore()
        fixture = load_fixture("python-merge")
        record = store.create_run(fixture.id, [])
        orchestrator = BenchOrchestrator(store)

        async def image_exists(args, timeout_seconds, container_name=None):
            return CommandResult(
                exit_code=0,
                stdout="",
                stderr="",
                duration_ms=1,
            )

        with patch("bench_daemon.executor._run_command", image_exists):
            await orchestrator._ensure_image(record, fixture, rebuild_image=False)

        self.assertEqual([event.event for event in record.events], ["image_build_skipped"])
        self.assertEqual(record.events[0].data["docker_image"], fixture.docker_image)

    async def test_emits_image_build_started_and_finished_when_image_is_missing(self):
        store = RunStore()
        fixture = load_fixture("python-merge")
        record = store.create_run(fixture.id, [])
        orchestrator = BenchOrchestrator(store)
        calls = []

        async def image_missing_then_builds(args, timeout_seconds, container_name=None):
            calls.append(args[:3])
            if args[:3] == ["docker", "image", "inspect"]:
                return CommandResult(1, "", "missing", 1)
            if args[:2] == ["docker", "build"]:
                return CommandResult(0, "built", "", 12.5)
            raise AssertionError(f"Unexpected command: {args}")

        with patch("bench_daemon.executor._run_command", image_missing_then_builds):
            await orchestrator._ensure_image(record, fixture, rebuild_image=False)

        self.assertEqual(
            [event.event for event in record.events],
            ["image_build_started", "image_build_finished"],
        )
        self.assertEqual(record.events[1].data["duration_ms"], 12.5)
        self.assertEqual(
            calls,
            [["docker", "image", "inspect"], ["docker", "build", "-f"]],
        )


if __name__ == "__main__":
    unittest.main()
