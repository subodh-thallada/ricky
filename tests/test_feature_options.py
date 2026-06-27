import unittest

from bench.services.feature_options import _parse_options


class FeatureOptionsTests(unittest.TestCase):
    def test_parse_options_accepts_literal_newlines_inside_generated_code(self):
        options = _parse_options(
            '{'
            '"suggestions": ['
            '{'
            '"id": "readable",'
            '"title": "Readable",'
            '"summary": "Clear implementation.",'
            '"implementationPlan": "Sort and merge.",'
            '"tradeoffs": ["Easy to read"],'
            '"generatedCode": "def merge_intervals(intervals):\n    return intervals\n"'
            '}'
            ']'
            '}'
        )

        self.assertEqual(len(options), 1)
        self.assertEqual(options[0].id, "readable")
        self.assertIn("def merge_intervals", options[0].generatedCode)

    def test_parse_options_recovers_complete_options_from_truncated_response(self):
        truncated = (
            '{'
            '"suggestions": ['
            '{'
            '"id": "readable",'
            '"title": "Readable",'
            '"summary": "Clear implementation.",'
            '"implementationPlan": "Sort and merge.",'
            '"tradeoffs": ["Easy to read"],'
            '"generatedCode": "def merge_intervals(intervals):\n    return intervals\n"'
            '},'
            '{'
            '"id": "fast",'
            '"title": "Fast",'
            '"summary": "Optimized implementation that got cut off mid-stream'
        )

        options = _parse_options(truncated)

        self.assertEqual(len(options), 1)
        self.assertEqual(options[0].id, "readable")
        self.assertIn("def merge_intervals", options[0].generatedCode)


if __name__ == "__main__":
    unittest.main()
