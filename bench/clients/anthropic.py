from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from anthropic import AsyncAnthropic

from bench.clients.types import TextGenerationResult
from bench.config import Settings


@dataclass
class AnthropicClient:
    """Cheap Claude chat model for user-facing text replies.

    Cerebras handles the heavy code/container work; this handles conversational
    chat using a low-cost Claude model (Haiku by default).
    """

    settings: Settings

    def _client(self) -> AsyncAnthropic:
        return AsyncAnthropic(api_key=self.settings.anthropic_api_key)

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        max_completion_tokens: int = 512,
        temperature: float = 0.2,
    ) -> TextGenerationResult:
        # Anthropic takes the system prompt as a top-level param, not a message.
        system_chunks = [m["content"] for m in messages if m["role"] == "system"]
        convo = [
            {"role": m["role"], "content": m["content"]}
            for m in messages
            if m["role"] in {"user", "assistant"}
        ]
        if not convo:
            convo = [{"role": "user", "content": ""}]

        kwargs: dict[str, Any] = {
            "model": self.settings.anthropic_model,
            "max_tokens": max_completion_tokens,
            "temperature": temperature,
            "messages": convo,
        }
        if system_chunks:
            kwargs["system"] = "\n\n".join(system_chunks)

        client = self._client()
        response = await client.messages.create(**kwargs)
        text = "".join(
            block.text for block in response.content if getattr(block, "type", None) == "text"
        )
        usage = getattr(response, "usage", None)
        return TextGenerationResult(
            model=response.model,
            text=text or None,
            usage=usage.model_dump() if usage is not None else None,
            raw=response,
        )

    async def check(self) -> dict[str, Any]:
        result: dict[str, Any] = {"configured": bool(self.settings.anthropic_api_key)}
        if not result["configured"]:
            result["status"] = "missing_api_key"
            return result
        try:
            data = await self.chat(
                [{"role": "user", "content": "Reply with OK only."}],
                max_completion_tokens=16,
                temperature=0.0,
            )
            result["status"] = "ok"
            result["model"] = data.model
            result["content"] = data.text
        except Exception as exc:  # pragma: no cover - SDK surface may vary
            result["status"] = "error"
            result["error"] = str(exc)
        return result
