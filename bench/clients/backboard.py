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


    async def remember(
        self,
        content: str,
        *,
        thread_id: str | None = None,
        assistant_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self.settings.backboard_api_key:
            return {"status": "skipped", "reason": "missing_api_key"}

        response = await self._client().send_message(
            content,
            thread_id=thread_id,
            assistant_id=assistant_id,
            llm_provider=self.settings.backboard_llm_provider,
            model_name=self.settings.backboard_model_name,
            stream=False,
            memory="Auto",
            send_to_llm="false",
            metadata={"source": "bench-memory", **(metadata or {})},
        )
        return {
            "status": "ok",
            "thread_id": _string_or_none(getattr(response, "thread_id", None)),
            "assistant_id": _string_or_none(getattr(response, "assistant_id", None)),
            "message_id": _string_or_none(getattr(response, "message_id", None)),
            "memory_operation_id": _string_or_none(getattr(response, "memory_operation_id", None)),
        }

    async def recall(
        self,
        *,
        assistant_id: str | None,
        query: str,
        limit: int = 5,
    ) -> list[str]:
        if not self.settings.backboard_api_key or not assistant_id or not query.strip():
            return []

        payload = await self._client().search_memories(
            assistant_id=assistant_id,
            query=query,
            limit=limit,
        )
        memories = payload.get("memories", []) if isinstance(payload, dict) else []
        output: list[str] = []
        for item in memories:
            if isinstance(item, dict):
                content = item.get("content") or item.get("text") or item.get("memory")
                if content:
                    output.append(str(content))
            elif item:
                output.append(str(item))
        return output

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


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)
