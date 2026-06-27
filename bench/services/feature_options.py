from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Any

from bench.clients.cerebras import CerebrasClient
from bench.clients.gemini import GeminiClient
from bench.config import Settings
from bench.schemas import (
    FeatureMetricSet,
    FeatureOption,
    FeatureOptionRequest,
    FeatureOptionResponse,
)
from bench.services.repo_context import build_repo_context


@dataclass
class FeatureOptionsService:
    settings: Settings

    def __post_init__(self) -> None:
        self.gemini = GeminiClient(self.settings)
        self.cerebras = CerebrasClient(self.settings)

    async def generate(self, request: FeatureOptionRequest) -> FeatureOptionResponse:
        repo_snapshot, context_metadata = self._repo_snapshot(request)
        gemini_plan = await self._gemini_plan(request, repo_snapshot)
        plans = _normalize_plans(gemini_plan)
        code_results = await asyncio.gather(
            *[self._cerebras_code(request, gemini_plan["contextSummary"], plan) for plan in plans]
        )

        options = [
            FeatureOption(
                id=plan["id"],
                title=plan["title"],
                summary=plan["summary"],
                implementationPlan=plan["implementationPlan"],
                tradeoffs=plan["tradeoffs"],
                generatedCode=code,
                metrics=FeatureMetricSet(**plan["metrics"]),
            )
            for plan, code in zip(plans, code_results, strict=True)
        ]

        return FeatureOptionResponse(
            assistantMessage=gemini_plan["assistantMessage"],
            contextSummary=gemini_plan["contextSummary"],
            contextMetadata=context_metadata,
            geminiModel=gemini_plan["geminiModel"],
            cerebrasModel=self.settings.cerebras_model,
            options=options,
        )

    def _repo_snapshot(self, request: FeatureOptionRequest) -> tuple[str, dict[str, object]]:
        parts: list[str] = []
        metadata: dict[str, object] = {
            "active_file_name": request.active_file_name,
            "repo_context_included": False,
        }
        if request.selected_text:
            parts.append(f"SELECTED TEXT:\n{request.selected_text}")
        if request.visible_text:
            parts.append(f"VISIBLE EDITOR TEXT:\n{request.visible_text[:6000]}")
        if request.repo_context is not None:
            repo_snapshot, repo_metadata = build_repo_context(request.repo_context)
            if repo_snapshot:
                parts.append(f"REPOSITORY SNIPPETS:\n{repo_snapshot}")
                metadata.update(repo_metadata)
                metadata["repo_context_included"] = True
        return "\n\n".join(parts), metadata

    async def _gemini_plan(self, request: FeatureOptionRequest, repo_snapshot: str) -> dict[str, Any]:
        system = (
            "You are Bench's Gemini planning layer. You talk to the user, condense codebase context, "
            "design implementation options, and assign mock metrics. You do not write final code. "
            "Return only valid JSON with no Markdown."
        )
        user = {
            "featureRequest": request.prompt,
            "language": request.language,
            "activeFileName": request.active_file_name,
            "repoSnapshot": repo_snapshot,
            "requiredJsonShape": {
                "assistantMessage": "friendly concise reply to the user",
                "contextSummary": "condensed codebase context for Cerebras",
                "options": [
                    {
                        "id": "stable-kebab-id",
                        "title": "option title",
                        "summary": "short user-facing summary",
                        "implementationPlan": "specific implementation plan, but no code",
                        "tradeoffs": ["tradeoff"],
                        "metrics": {
                            "readability": 80,
                            "simplicity": 80,
                            "speed": 80,
                            "memory": 80,
                            "maintainability": 80,
                            "testConfidence": 80,
                        },
                    }
                ],
            },
        }
        response = await self.gemini.chat(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(user)},
            ],
            max_completion_tokens=1800,
            temperature=0.2,
        )
        payload = _parse_json_object(response.text or "")
        payload["geminiModel"] = response.model
        return payload

    async def _cerebras_code(
        self,
        request: FeatureOptionRequest,
        context_summary: str,
        plan: dict[str, Any],
    ) -> str:
        system = (
            "You are Bench's Cerebras code writer. Write code only. "
            "Do not produce metrics, conversation, or analysis. "
            "Return a single code artifact with no Markdown fence."
        )
        user = {
            "featureRequest": request.prompt,
            "language": request.language,
            "activeFileName": request.active_file_name,
            "condensedContextFromGemini": context_summary,
            "implementationOption": {
                "title": plan["title"],
                "summary": plan["summary"],
                "implementationPlan": plan["implementationPlan"],
                "tradeoffs": plan["tradeoffs"],
            },
        }
        response = await self.cerebras.chat(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(user)},
            ],
            max_completion_tokens=1800,
            reasoning_effort="low",
            temperature=0.25,
        )
        return _strip_code_fence(response.text or "")


def _parse_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned, flags=re.IGNORECASE).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not match:
            raise
        parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise ValueError("Gemini response was not a JSON object.")
    return parsed


def _normalize_plans(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw_options = payload.get("options")
    if not isinstance(raw_options, list) or not raw_options:
        raise ValueError("Gemini response did not include options.")

    plans: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_options[:4]):
        if not isinstance(raw, dict):
            continue
        title = str(raw.get("title") or f"Option {index + 1}").strip()
        metrics = raw.get("metrics") if isinstance(raw.get("metrics"), dict) else {}
        plans.append(
            {
                "id": _slug(str(raw.get("id") or title or f"option-{index + 1}"), index),
                "title": title,
                "summary": str(raw.get("summary") or "").strip(),
                "implementationPlan": str(raw.get("implementationPlan") or "").strip(),
                "tradeoffs": [str(item) for item in raw.get("tradeoffs", []) if str(item).strip()],
                "metrics": {
                    "readability": _metric(metrics.get("readability"), 78 + index),
                    "simplicity": _metric(metrics.get("simplicity"), 74 + index),
                    "speed": _metric(metrics.get("speed"), 72 + index),
                    "memory": _metric(metrics.get("memory"), 76 + index),
                    "maintainability": _metric(metrics.get("maintainability"), 80 + index),
                    "testConfidence": _metric(metrics.get("testConfidence"), 70 + index),
                },
            }
        )
    if not plans:
        raise ValueError("Gemini response did not include usable options.")
    return plans


def _metric(value: Any, fallback: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = fallback
    return max(0, min(100, number))


def _slug(value: str, index: int) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or f"option-{index + 1}"


def _strip_code_fence(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return cleaned
