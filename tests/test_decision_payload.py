import unittest

from bench_daemon.models import CandidateRecord, RunRecord


class DecisionPayloadTests(unittest.TestCase):
    def test_code_bundle_returns_raw_source_for_single_file_candidate(self):
        candidate = CandidateRecord(
            candidate_id="readable",
            label="Readable",
            rationale=None,
            code="",
            files={"candidate_target.py": "def merge_intervals(intervals):\n    return intervals\n"},
        )

        self.assertEqual(
            candidate.code_bundle(),
            "def merge_intervals(intervals):\n    return intervals\n",
        )

    def test_code_bundle_includes_all_files_for_multi_file_candidate(self):
        candidate = CandidateRecord(
            candidate_id="multi",
            label="Multi",
            rationale=None,
            code="",
            files={
                "candidate_target.py": "def merge_intervals(intervals):\n    return intervals\n",
                "support.py": "VALUE = 1\n",
            },
        )

        bundle = candidate.code_bundle()

        self.assertIn("### candidate_target.py", bundle)
        self.assertIn("def merge_intervals", bundle)
        self.assertIn("### support.py", bundle)
        self.assertIn("VALUE = 1", bundle)

    def test_candidate_payload_keeps_compact_summary_and_detail_urls(self):
        record = RunRecord(run_id="run_test", fixture_id="python-merge")
        candidate = CandidateRecord(
            candidate_id="readable",
            label="Readable",
            rationale="Clear implementation.",
            code="def merge_intervals(intervals):\n    return intervals\n",
        )
        candidate.status = "passed"
        candidate.exit_code = 0
        candidate.duration_ms = 1.2
        candidate.tests = {"passed": 8, "failed": 0, "total": 8}
        candidate.metrics = {"runtime_p95_ms": 1.1}
        record.set_candidates([candidate])
        record.status = "completed"
        record.winner_candidate_id = "readable"
        record.summary = "Readable passed all tests."
        record.recommended_next_action = "Return evidence to coding agent"

        payload = record.to_payload()

        self.assertEqual(payload["run_id"], "run_test")
        self.assertEqual(payload["winner_candidate_id"], "readable")
        self.assertEqual(payload["recommended_next_action"], "Return evidence to coding agent")
        self.assertEqual(
            payload["available_actions"],
            [
                {
                    "action": "return_evidence",
                    "label": "Return evidence to coding agent",
                },
                {
                    "action": "apply_candidate",
                    "label": "Offer apply winner",
                    "candidate_id": "readable",
                },
            ],
        )
        self.assertEqual(payload["candidates"][0]["candidate_id"], "readable")
        self.assertEqual(payload["candidates"][0]["logs_url"], "/runs/run_test/candidates/readable/logs")
        self.assertEqual(payload["candidates"][0]["code_url"], "/runs/run_test/candidates/readable/code")
        self.assertEqual(payload["candidates"][0]["metrics"], {"runtime_p95_ms": 1.1})

    def test_payload_omits_apply_action_when_winner_did_not_pass(self):
        record = RunRecord(run_id="run_test", fixture_id="python-merge")
        candidate = CandidateRecord(
            candidate_id="broken",
            label="Broken",
            rationale=None,
            code="def merge_intervals(intervals):\n    return []\n",
        )
        candidate.status = "failed"
        record.set_candidates([candidate])
        record.status = "completed"
        record.winner_candidate_id = "broken"
        record.recommended_next_action = "Return evidence to coding agent"

        payload = record.to_payload()

        self.assertEqual(
            payload["available_actions"],
            [
                {
                    "action": "return_evidence",
                    "label": "Return evidence to coding agent",
                }
            ],
        )

    def test_payload_omits_actions_for_run_failure(self):
        record = RunRecord(run_id="run_test", fixture_id="python-merge")
        record.status = "failed"
        record.error = "Docker image build failed"
        record.recommended_next_action = "Inspect run failure"

        payload = record.to_payload()

        self.assertEqual(payload["available_actions"], [])
        self.assertEqual(payload["recommended_next_action"], "Inspect run failure")


if __name__ == "__main__":
    unittest.main()
