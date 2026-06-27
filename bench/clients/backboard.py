from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backboard import BackboardClient

from bench.config import Settings


@dataclass
class BackboardAdapter:
    settings: Settings

    def _client(self) -> BackboardClient:
        return BackboardClient(api_key=self.settings.backboard_api_key)

    async def check(self) -> dict[str, Any]:
        result = {"configured": bool(self.settings.backboard_api_key)}
        if not result["configured"]:
            result["status"] = "missing_api_key"
            return result

        client = self._client()
        try:
            response = await client.send_message(
                "Reply with OK only.",
                llm_provider=self.settings.backboard_llm_provider,
                model_name=self.settings.backboard_model_name,
                stream=False,
            )
            result["status"] = "ok"
            result["content"] = response.content
            result["thread_id"] = getattr(response, "thread_id", None)
            result["assistant_id"] = getattr(response, "assistant_id", None)
        except Exception as exc:  # pragma: no cover - SDK surface may vary
            result["status"] = "error"
            result["error"] = str(exc)
        return result
