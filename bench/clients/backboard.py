from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backboard import BackboardClient

from bench.config import Settings
from bench.schemas import FeatureOption


@dataclass
class BackboardAdapter:
    settings: Settings

    def _client(self) -> BackboardClient:
        return BackboardClient(api_key=self.settings.backboard_api_key)

    async def _get_or_create_assistant_id(self) -> str:
        client = self._client()
        name = self.settings.backboard_assistant_name
        assistants = await client.list_assistants()
        for assistant in assistants:
            if assistant.name == name:
                return str(assistant.assistant_id)

        assistant = await client.create_assistant(
            name=name,
            description="Stores Bench implementation-option decisions and preference signals.",
            system_prompt=(
                "You remember which implementation option a developer accepted, why it won, "
                "and which metrics or tradeoffs mattered. Keep memories concise and preference-focused."
            ),
        )
        return str(assistant.assistant_id)

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

    async def search_decision_memories(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        if not self.settings.backboard_api_key:
            return []

        try:
            assistant_id = await self._get_or_create_assistant_id()
            response = await self._client().search_memories(assistant_id, query=query, limit=limit)
        except Exception:
            return []

        memories = response.get("memories", []) if isinstance(response, dict) else []
        return [memory for memory in memories if isinstance(memory, dict)]

    async def remember_decision(
        self,
        *,
        feature_request: str,
        selected_option: FeatureOption,
        all_options: list[FeatureOption],
    ) -> dict[str, Any]:
        if not self.settings.backboard_api_key:
            return {"status": "skipped", "reason": "missing_api_key"}

        assistant_id = await self._get_or_create_assistant_id()
        selected_metrics = selected_option.metrics.model_dump(by_alias=True)
        implementation_plan = _option_value(selected_option, "implementationPlan", "implementation_plan")
        alternatives = [
            option.title
            for option in all_options
            if option.id != selected_option.id
        ]
        content = (
            "Bench implementation choice accepted.\n"
            f"Feature request: {feature_request}\n"
            f"Chosen option: {selected_option.title}\n"
            f"Chosen summary: {selected_option.summary}\n"
            f"Implementation plan: {implementation_plan}\n"
            f"Tradeoffs: {'; '.join(selected_option.tradeoffs)}\n"
            f"Metrics: {selected_metrics}\n"
            f"Rejected alternatives: {'; '.join(alternatives) if alternatives else 'none'}\n"
            "Preference signal: future recommendations should favor options with similar tradeoffs and metric strengths "
            "when they fit the new request."
        )
        metadata = {
            "kind": "bench_implementation_choice",
            "feature_request": feature_request,
            "selected_option_id": selected_option.id,
            "selected_option_title": selected_option.title,
            "metrics": selected_metrics,
        }
        return await self._client().add_memory(
            assistant_id,
            content=content,
            metadata=metadata,
        )


def _option_value(option: FeatureOption, *names: str) -> str:
    for name in names:
        value = getattr(option, name, None)
        if isinstance(value, str):
            return value
    return ""
