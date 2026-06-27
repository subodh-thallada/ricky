import unittest

from candidate_target import merge_intervals


class MergeIntervalsTest(unittest.TestCase):
    def test_empty_input(self):
        self.assertEqual(merge_intervals([]), [])

    def test_single_interval(self):
        self.assertEqual(merge_intervals([[1, 3]]), [[1, 3]])

    def test_merges_overlaps(self):
        self.assertEqual(
            merge_intervals([[1, 3], [2, 6], [8, 10], [15, 18]]),
            [[1, 6], [8, 10], [15, 18]],
        )

    def test_merges_touching_intervals(self):
        self.assertEqual(
            merge_intervals([[1, 4], [4, 5]]),
            [[1, 5]],
        )

    def test_sorts_unsorted_input(self):
        self.assertEqual(
            merge_intervals([[8, 10], [1, 3], [2, 6], [15, 18]]),
            [[1, 6], [8, 10], [15, 18]],
        )

    def test_preserves_negative_ranges(self):
        self.assertEqual(
            merge_intervals([[-10, -5], [-6, -1], [0, 2]]),
            [[-10, -1], [0, 2]],
        )

    def test_does_not_mutate_input(self):
        intervals = [[1, 4], [2, 3], [9, 10]]
        original = [interval[:] for interval in intervals]

        merge_intervals(intervals)

        self.assertEqual(intervals, original)

    def test_large_disjoint_input(self):
        intervals = [[index * 3, index * 3 + 1] for index in range(500)]
        self.assertEqual(merge_intervals(intervals), intervals)


if __name__ == "__main__":
    unittest.main()
