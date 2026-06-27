import unittest

from bench_daemon.executor import (
    CommandResult,
    _parse_docker_mem_usage,
    _resolve_peak_memory_kb,
    apply_command_result,
    parse_runner_output,
)
from bench_daemon.models import CandidateRecord


class RunnerOutputTests(unittest.TestCase):
    def test_parses_final_stdout_line_as_json(self):
        result = CommandResult(
            exit_code=0,
            stdout='test output\n{"duration_ms": 1.2, "tests": {"passed": 8, "failed": 0, "total": 8}}\n',
            stderr="",
            duration_ms=20,
        )

        logs, parsed, error = parse_runner_output(result)

        self.assertEqual(logs, "test output\n")
        self.assertEqual(parsed["tests"]["passed"], 8)
        self.assertEqual(error, "")

    def test_returns_parse_error_for_missing_json(self):
        result = CommandResult(
            exit_code=1,
            stdout="plain text\n",
            stderr="",
            duration_ms=20,
        )

        _, parsed, error = parse_runner_output(result)

        self.assertIsNone(parsed)
        self.assertIn("invalid final JSON line", error)

    def test_applies_structured_failures_errors_and_metrics(self):
        candidate = CandidateRecord("broken", "Broken", None, "")
        result = CommandResult(
            exit_code=1,
            stdout=(
                "runner log\n"
                '{"duration_ms": 3.5, "tests": {"passed": 7, "failed": 1, "total": 8}, '
                '"failures": [{"test": "test_empty_input", "details": "expected []"}], '
                '"errors": [], "metrics": {"runtime_p95_ms": 2.1}}\n'
            ),
            stderr="stderr log\n",
            duration_ms=20,
        )

        apply_command_result(candidate, result)

        self.assertEqual(candidate.status, "failed")
        self.assertEqual(candidate.duration_ms, 3.5)
        self.assertEqual(candidate.tests, {"passed": 7, "failed": 1, "total": 8})
        self.assertEqual(candidate.failures[0]["test"], "test_empty_input")
        self.assertEqual(candidate.metrics["runtime_p95_ms"], 2.1)
        self.assertIn("runner log", candidate.logs)
        self.assertIn("stderr log", candidate.logs)

    def test_marks_timeout_as_candidate_level_result(self):
        candidate = CandidateRecord("slow", "Slow", None, "")
        result = CommandResult(
            exit_code=-1,
            stdout="partial log\n",
            stderr="",
            duration_ms=5000,
            timed_out=True,
            peak_container_memory_kb=8192,
        )

        apply_command_result(candidate, result)

        self.assertEqual(candidate.status, "timeout")
        self.assertEqual(candidate.exit_code, -1)
        self.assertEqual(candidate.peak_memory_kb, 8192)
        self.assertEqual(candidate.tests, {"passed": 0, "failed": 1, "total": 1})

    def test_applies_peak_memory_kb_from_runner_payload(self):
        candidate = CandidateRecord("fast", "Fast", None, "")
        result = CommandResult(
            exit_code=0,
            stdout=(
                "runner log\n"
                '{"duration_ms": 12.5, "peak_memory_kb": 4096, '
                '"tests": {"passed": 4, "failed": 0, "total": 4}}\n'
            ),
            stderr="",
            duration_ms=20,
            peak_container_memory_kb=5120,
        )

        apply_command_result(candidate, result)

        self.assertEqual(candidate.status, "passed")
        self.assertEqual(candidate.peak_memory_kb, 5120)

    def test_prefers_runner_peak_when_container_stats_missing(self):
        parsed = {"peak_memory_kb": 2048}
        result = CommandResult(
            exit_code=0,
            stdout="",
            stderr="",
            duration_ms=1,
        )
        self.assertEqual(_resolve_peak_memory_kb(parsed, result), 2048)

    def test_parses_docker_mem_usage(self):
        self.assertEqual(_parse_docker_mem_usage("12.5MiB / 256MiB"), 12800)
        self.assertEqual(_parse_docker_mem_usage("512KiB / 256MiB"), 512)
        self.assertIsNone(_parse_docker_mem_usage(""))


if __name__ == "__main__":
    unittest.main()
