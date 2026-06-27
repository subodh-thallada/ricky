import unittest

from bench_daemon.models import CandidateRecord, build_summary, rank_candidates


class RankingTests(unittest.TestCase):
    def test_passed_candidates_rank_before_faster_failures(self):
        broken = CandidateRecord("broken", "Broken", None, "", status="failed")
        broken.duration_ms = 1
        passed = CandidateRecord("passed", "Passed", None, "", status="passed")
        passed.duration_ms = 100

        ranked = rank_candidates([broken, passed])

        self.assertEqual([candidate.candidate_id for candidate in ranked], ["passed", "broken"])

    def test_shorter_duration_wins_within_same_status(self):
        slow = CandidateRecord("slow", "Slow", None, "", status="passed")
        slow.duration_ms = 10
        fast = CandidateRecord("fast", "Fast", None, "", status="passed")
        fast.duration_ms = 2

        ranked = rank_candidates([slow, fast])

        self.assertEqual([candidate.candidate_id for candidate in ranked], ["fast", "slow"])

    def test_summary_recommends_apply_for_passing_winner(self):
        winner = CandidateRecord("fast", "Fast", None, "", status="passed")
        winner.duration_ms = 2

        winner_id, summary, action = build_summary([winner])

        self.assertEqual(winner_id, "fast")
        self.assertIn("Fast passed all tests", summary)
        self.assertEqual(action, "Return evidence to coding agent")

    def test_summary_preserves_backend_action_when_no_candidate_passed(self):
        failed = CandidateRecord("broken", "Broken", None, "", status="failed")
        failed.duration_ms = 2

        winner_id, summary, action = build_summary([failed])

        self.assertEqual(winner_id, "broken")
        self.assertIn("No candidate passed", summary)
        self.assertEqual(action, "Return evidence to coding agent")


if __name__ == "__main__":
    unittest.main()
