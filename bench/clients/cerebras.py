from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from cerebras.cloud.sdk import Cerebras

from bench.clients.types import TextGenerationResult
from bench.config import Settings


class CerebrasError(RuntimeError):
    """Raised when Cerebras returns a non-success response."""


@dataclass
class CerebrasClient:
    settings: Settings

    def _client(self) -> Cerebras:
        return Cerebras(api_key=self.settings.cerebras_api_key)

    async def list_models(self) -> Any:
        client = self._client()
        return await asyncio.to_thread(client.models.list)

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        max_completion_tokens: int = 128,
        reasoning_effort: str = "low",
        temperature: float = 0.2,
    ) -> TextGenerationResult:
        client = self._client()

        def _run() -> Any:
            return client.chat.completions.create(
                model=self.settings.cerebras_model,
                messages=messages,
                max_completion_tokens=max_completion_tokens,
                reasoning_effort=reasoning_effort,
                temperature=temperature,
            )

        try:
            response = await asyncio.to_thread(_run)
        except Exception as exc:  # pragma: no cover - SDK exceptions vary
            raise CerebrasError(f"Cerebras request failed: {exc}") from exc
        return TextGenerationResult(
            model=response.model,
            text=response.choices[0].message.content,
            usage=response.usage.model_dump() if response.usage else None,
            raw=response,
        )

    async def check(self) -> dict[str, Any]:
        result = {"configured": bool(self.settings.cerebras_api_key)}
        if not result["configured"]:
            result["status"] = "missing_api_key"
            return result
        try:
            data = await self.chat(
                [{"role": "user", "content": "Reply with OK only."}],
                max_completion_tokens=16,
                reasoning_effort="low",
            )
            result["status"] = "ok"
            result["model"] = data.model
            result["content"] = data.text
            result["usage"] = data.usage
            models = await self.list_models()
            result["available_models"] = [model.id for model in models.data]
        except CerebrasError as exc:
            result["status"] = "error"
            result["error"] = str(exc)
        return result
