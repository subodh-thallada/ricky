import unittest

from bench_daemon.executor import CommandResult, parse_runner_output


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


if __name__ == "__main__":
    unittest.main()
