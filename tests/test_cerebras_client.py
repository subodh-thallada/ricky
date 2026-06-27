import unittest
from types import SimpleNamespace

from bench.clients.cerebras import CerebrasClient
from bench.config import Settings


class _CapturingCompletions:
    def __init__(self):
        self.request = None

    def create(self, **kwargs):
        self.request = kwargs
        return SimpleNamespace(
            model="zai-glm-4.7",
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"suggestions":[]}'))],
            usage=None,
        )


class _TestClient(CerebrasClient):
    def __init__(self):
        super().__init__(Settings(cerebras_api_key="test"))
        self.completions = _CapturingCompletions()

    def _client(self):
        return SimpleNamespace(chat=SimpleNamespace(completions=self.completions))


class CerebrasClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_disable_reasoning_omits_reasoning_effort(self):
        client = _TestClient()

        await client.chat(
            [{"role": "user", "content": "Return JSON"}],
            reasoning_effort="low",
            disable_reasoning=True,
            response_format={"type": "json_object"},
        )

        self.assertTrue(client.completions.request["disable_reasoning"])
        self.assertNotIn("reasoning_effort", client.completions.request)
        self.assertEqual(client.completions.request["response_format"], {"type": "json_object"})


if __name__ == "__main__":
    unittest.main()
