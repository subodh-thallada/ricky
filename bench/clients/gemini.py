from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from bench.clients.types import TextGenerationResult
from bench.config import Settings


class GeminiError(RuntimeError):
    """Raised when Gemini returns a non-success response."""


@dataclass
class GeminiClient:
    settings: Settings

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        max_completion_tokens: int = 512,
        temperature: float = 0.2,
    ) -> TextGenerationResult:
        system_chunks = [m["content"] for m in messages if m["role"] == "system"]
        non_system = [m for m in messages if m["role"] != "system"]
        prompt_parts: list[str] = []
        for msg in non_system:
            role = msg["role"].upper()
            prompt_parts.append(f"{role}:\n{msg['content']}")

        payload: dict[str, Any] = {
            "contents": [{"parts": [{"text": "\n\n".join(prompt_parts)}]}],
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_completion_tokens,
                # Gemini-specific optimization: turn off thinking to minimize
                # free-tier token usage on gemini-2.5-flash. Cerebras uses the
                # separate reasoning_effort parameter instead.
                "thinkingConfig": {"thinkingBudget": 0},
            },
        }
        if system_chunks:
            payload["systemInstruction"] = {"parts": [{"text": "\n\n".join(system_chunks)}]}

        async with httpx.AsyncClient(timeout=45.0) as client:
            response = await client.post(
                f"{self.settings.gemini_base_url}/models/{self.settings.gemini_model}:generateContent",
                headers={
                    "Content-Type": "application/json",
                    "X-goog-api-key": self.settings.gemini_api_key,
                },
                json=payload,
            )
        if not response.is_success:
            raise GeminiError(
                f"Gemini request failed with {response.status_code}: {response.text}"
            )

        body = response.json()
        text = None
        for candidate in body.get("candidates", []):
            for part in candidate.get("content", {}).get("parts", []):
                if "text" in part:
                    text = (text or "") + part["text"]
        return TextGenerationResult(
            model=body.get("modelVersion", self.settings.gemini_model),
            text=text,
            usage=body.get("usageMetadata"),
            raw=body,
        )

    async def check(self) -> dict[str, Any]:
        result = {"configured": bool(self.settings.gemini_api_key)}
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
            result["usage"] = data.usage
        except GeminiError as exc:
            result["status"] = "error"
            result["error"] = str(exc)
        return result
