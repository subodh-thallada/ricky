import unittest

from bench_daemon.fixtures import (
    FixtureError,
    load_candidate_files,
    load_fixture,
    parse_candidate_request,
)


class FixtureTests(unittest.TestCase):
    def test_loads_python_merge_fixture(self):
        fixture = load_fixture("python-merge")

        self.assertEqual(fixture.id, "python-merge")
        self.assertEqual(fixture.target_file, "candidate_target.py")
        self.assertEqual(fixture.runner, "python bench_runner.py")

    def test_loads_all_fixture_candidates(self):
        fixture = load_fixture("python-merge")

        candidates = load_candidate_files(fixture)

        self.assertEqual(
            [candidate.candidate_id for candidate in candidates],
            ["broken_edge_case", "fast", "readable", "slow"],
        )

    def test_rejects_candidate_without_target_file(self):
        fixture = load_fixture("python-merge")

        with self.assertRaises(FixtureError):
            parse_candidate_request(
                fixture,
                [
                    {
                        "candidate_id": "bad",
                        "files": {"other.py": "print('nope')"},
                    }
                ],
            )

    def test_rejects_duplicate_candidate_ids(self):
        fixture = load_fixture("python-merge")

        with self.assertRaises(FixtureError):
            parse_candidate_request(
                fixture,
                [
                    {
                        "candidate_id": "same",
                        "files": {"candidate_target.py": "def merge_intervals(intervals):\n    return []\n"},
                    },
                    {
                        "candidate_id": "same",
                        "files": {"candidate_target.py": "def merge_intervals(intervals):\n    return intervals\n"},
                    },
                ],
            )

    def test_rejects_unsafe_candidate_paths(self):
        fixture = load_fixture("python-merge")

        with self.assertRaises(FixtureError):
            parse_candidate_request(
                fixture,
                [
                    {
                        "candidate_id": "bad",
                        "files": {
                            "candidate_target.py": "def merge_intervals(intervals):\n    return []\n",
                            "../escape.py": "print('nope')",
                        },
                    }
                ],
            )

    def test_rejects_invalid_candidate_metadata(self):
        fixture = load_fixture("python-merge")

        with self.assertRaises(FixtureError):
            parse_candidate_request(
                fixture,
                [
                    {
                        "candidate_id": "bad",
                        "label": 42,
                        "files": {"candidate_target.py": "def merge_intervals(intervals):\n    return []\n"},
                    }
                ],
            )


if __name__ == "__main__":
    unittest.main()
