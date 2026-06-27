import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from bench.schemas import FeatureOptionsRequest, RepoContextConfig
from bench.services.feature_options import FeatureOptionsService, _parse_options


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

    def test_parse_options_caps_provider_output_at_three(self):
        suggestions = [
            {
                "id": f"option-{index}",
                "title": f"Option {index}",
                "summary": "Different tradeoff.",
                "implementationPlan": "Implement it.",
                "tradeoffs": ["Tradeoff"],
                "generatedCode": f"def option_{index}():\n    pass\n",
            }
            for index in range(4)
        ]

        import json

        self.assertEqual(len(_parse_options(json.dumps({"suggestions": suggestions}))), 3)


class FeatureOptionsRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_test_prompt_uses_local_demo_without_calling_cerebras(self):
        class FailingClient:
            async def chat(self, *args, **kwargs):
                raise AssertionError("Cerebras must not run for an explicit (test) prompt")

        with TemporaryDirectory() as tmpdir:
            Path(tmpdir, "students.py").write_text("def get_students():\n    pass\n", encoding="utf-8")
            response = await FeatureOptionsService(FailingClient()).generate(
                FeatureOptionsRequest(
                    prompt="Implement a get students endpoint (test)",
                    repo_context=RepoContextConfig(root_path=tmpdir),
                )
            )

        self.assertEqual(len(response.options), 3)
        self.assertTrue(all("### src/get_students_demo.py" in option.generated_code for option in response.options))

    async def test_standard_prompt_calls_cerebras_and_keeps_file_aware_output(self):
        class FakeClient:
            calls = 0

            async def chat(self, *args, **kwargs):
                self.calls += 1
                return SimpleNamespace(
                    model="zai-glm-4.7",
                    text='{"suggestions":[{"id":"one","title":"One","summary":"A","implementationPlan":"P","tradeoffs":["T"],"generatedCode":"### src/new_file.py\\n```python\\ndef created():\\n    return True\\n```"},{"id":"two","title":"Two","summary":"B","implementationPlan":"P","tradeoffs":["T"],"generatedCode":"### src/existing.py\\n```python\\ndef updated():\\n    return True\\n```"}]}',
                )

        client = FakeClient()
        with TemporaryDirectory() as tmpdir:
            response = await FeatureOptionsService(client).generate(
                FeatureOptionsRequest(
                    prompt="Implement the feature",
                    repo_context=RepoContextConfig(root_path=tmpdir),
                )
            )

        self.assertEqual(client.calls, 1)
        self.assertEqual(len(response.options), 2)
        self.assertIn("### src/new_file.py", response.options[0].generated_code)

    async def test_single_strong_provider_option_is_returned_without_retry(self):
        class FakeClient:
            calls = 0

            async def chat(self, *args, **kwargs):
                self.calls += 1
                return SimpleNamespace(
                    model="zai-glm-4.7",
                    text='{"suggestions":[{"id":"focused","title":"Focused solution","summary":"The clear choice.","implementationPlan":"Implement the focused change.","tradeoffs":["No artificial variants"],"generatedCode":"def focused():\\n    return True\\n"}]}',
                )

        client = FakeClient()
        with TemporaryDirectory() as tmpdir:
            response = await FeatureOptionsService(client).generate(
                FeatureOptionsRequest(
                    prompt="Implement the clear-cut change",
                    repo_context=RepoContextConfig(root_path=tmpdir),
                )
            )

        self.assertEqual(client.calls, 1)
        self.assertEqual(len(response.options), 1)
        self.assertEqual(response.options[0].id, "focused")

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
