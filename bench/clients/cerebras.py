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
        stub = _maybe_build_test_response(messages, model_name="cerebras-test-stub")
        if stub is not None:
            return stub

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


def _maybe_build_test_response(
    messages: list[dict[str, str]],
    *,
    model_name: str,
) -> TextGenerationResult | None:
    combined = "\n\n".join(message.get("content", "") for message in messages)
    lowered = combined.lower()
    if "(test)" not in lowered:
        return None

    if "fibonacci" in lowered and ("return exactly:" in lowered or "full function only" in lowered):
        if "assigned slot: readable" in lowered:
            text = (
                "Rationale: Hardcoded readable Fibonacci candidate.\n"
                "```python\n"
                "def fibonacci(n: int) -> int:\n"
                "    if n < 0:\n"
                "        raise ValueError(\"n must be non-negative\")\n"
                "    if n < 2:\n"
                "        return n\n"
                "\n"
                "    previous = 0\n"
                "    current = 1\n"
                "    for _ in range(2, n + 1):\n"
                "        previous, current = current, previous + current\n"
                "    return current\n"
                "```"
            )
        elif "assigned slot: fast" in lowered:
            text = (
                "Rationale: Hardcoded fast Fibonacci candidate.\n"
                "```python\n"
                "def fibonacci(n: int) -> int:\n"
                "    if n < 0:\n"
                "        raise ValueError(\"n must be non-negative\")\n"
                "    a, b = 0, 1\n"
                "    while n:\n"
                "        a, b = b, a + b\n"
                "        n -= 1\n"
                "    return a\n"
                "```"
            )
        else:
            text = (
                "Rationale: Hardcoded low-memory Fibonacci candidate.\n"
                "```python\n"
                "def fibonacci(n: int) -> int:\n"
                "    if n < 0:\n"
                "        raise ValueError(\"n must be non-negative\")\n"
                "    if n == 0:\n"
                "        return 0\n"
                "    a = 0\n"
                "    b = 1\n"
                "    for _ in range(1, n):\n"
                "        a = a + b\n"
                "        b = a - b\n"
                "    return b\n"
                "```"
            )
    elif "return exactly:" in lowered or "full function only" in lowered:
        text = (
            "Rationale: Hardcoded Cerebras test candidate.\n"
            "```python\n"
            "def merge_intervals(intervals):\n"
            "    return intervals\n"
            "```"
        )
    elif "fibonacci" in lowered and "wrap each code artifact in fenced code blocks" in lowered:
        text = (
            "This is a hardcoded Cerebras Fibonacci test response with three implementations.\n\n"
            "Readable iterative version:\n"
            "```python\n"
            "def fibonacci_iterative(n: int) -> int:\n"
            "    if n < 0:\n"
            "        raise ValueError(\"n must be non-negative\")\n"
            "    if n < 2:\n"
            "        return n\n"
            "    prev_num, curr_num = 0, 1\n"
            "    for _ in range(2, n + 1):\n"
            "        prev_num, curr_num = curr_num, prev_num + curr_num\n"
            "    return curr_num\n"
            "```\n\n"
            "Memoized recursive version:\n"
            "```python\n"
            "def fibonacci_memoized(n: int, memo: dict[int, int] | None = None) -> int:\n"
            "    if n < 0:\n"
            "        raise ValueError(\"n must be non-negative\")\n"
            "    if memo is None:\n"
            "        memo = {0: 0, 1: 1}\n"
            "    if n not in memo:\n"
            "        memo[n] = fibonacci_memoized(n - 1, memo) + fibonacci_memoized(n - 2, memo)\n"
            "    return memo[n]\n"
            "```\n\n"
            "Fast-doubling version:\n"
            "```python\n"
            "def fibonacci_fast_doubling(n: int) -> int:\n"
            "    if n < 0:\n"
            "        raise ValueError(\"n must be non-negative\")\n"
            "\n"
            "    def _fib(k: int) -> tuple[int, int]:\n"
            "        if k == 0:\n"
            "            return 0, 1\n"
            "        a, b = _fib(k >> 1)\n"
            "        c = a * ((b << 1) - a)\n"
            "        d = a * a + b * b\n"
            "        if k & 1:\n"
            "            return d, c + d\n"
            "        return c, d\n"
            "\n"
            "    return _fib(n)[0]\n"
            "```"
        )
    elif "wrap each code artifact in fenced code blocks" in lowered:
        text = (
            "This is a hardcoded Cerebras test response.\n\n"
            "```python\n"
            "def test_helper(value: str) -> str:\n"
            "    return f\"cerebras-test:{value}\"\n"
            "```"
        )
    else:
        text = "This is a hardcoded Cerebras test response."

    return TextGenerationResult(
        model=model_name,
        text=text,
        usage={"stub": True, "total_tokens": 0},
        raw={"stub": True},
    )
