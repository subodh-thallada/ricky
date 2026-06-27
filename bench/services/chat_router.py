from __future__ import annotations

from bench.clients.anthropic import AnthropicClient
from bench.clients.backboard import BackboardAdapter
from bench.clients.cerebras import CerebrasClient
from bench.config import Settings


CODE_HINTS = {
    "code",
    "implement",
    "write",
    "generate",
    "function",
    "class",
    "refactor",
    "patch",
    "edit",
    "fix",
    "endpoint",
    "api",
    "component",
    "script",
}


class ChatRouter:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.backboard = BackboardAdapter(settings)
        self.cerebras = CerebrasClient(settings)
        self.backboard = BackboardAdapter(settings)
        self.anthropic = AnthropicClient(settings)

    def detect_mode(self, prompt: str, *, intent_hint: str = "auto") -> str:
        if intent_hint in {"text", "code"}:
            return intent_hint
        lowered = prompt.lower()
        if any(hint in lowered for hint in CODE_HINTS):
            return "code"
        return "text"

    def provider_for_mode(self, mode: str) -> tuple[str, object]:
        if mode == "code":
            # Cerebras handles heavy code/container work.
            return "cerebras", self.cerebras
        # Cheap Claude model handles user-facing conversational chat.
        return "anthropic", self.anthropic
