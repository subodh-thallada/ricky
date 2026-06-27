import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bench_daemon.executor import create_candidate_workspace
from bench_daemon.fixtures import CandidateInput, load_fixture


class WorkspaceTests(unittest.TestCase):
    def test_materializes_same_fixture_snapshot_with_candidate_replacements(self):
        fixture = load_fixture("python-merge")
        candidate = CandidateInput(
            candidate_id="explicit",
            label="Explicit",
            rationale=None,
            files={
                "candidate_target.py": "def merge_intervals(intervals):\n    return intervals\n",
                "notes/extra.txt": "extra evidence\n",
            },
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("bench_daemon.executor.LOCAL_RUNS_ROOT", Path(tmpdir)):
                workspace = create_candidate_workspace("run_test", fixture, candidate)

            self.assertTrue((workspace / "bench_runner.py").is_file())
            self.assertTrue((workspace / "test_candidate.py").is_file())
            self.assertFalse((workspace / fixture.candidates_dir).exists())
            self.assertEqual(
                (workspace / "candidate_target.py").read_text(encoding="utf-8"),
                "def merge_intervals(intervals):\n    return intervals\n",
            )
            self.assertEqual(
                (workspace / "notes/extra.txt").read_text(encoding="utf-8"),
                "extra evidence\n",
            )

if __name__ == "__main__":
    unittest.main()
