from __future__ import annotations

import asyncio

from bench.clients.types import TextGenerationResult
from bench.schemas import BenchRunPreviewResponse, CandidatePreview, ConversationMessage, RepoContextConfig
from bench.services.repo_context import build_repo_context


OBJECTIVES: list[tuple[str, str]] = [
    ("readable", "Optimize for the next human to read this code. Prefer clarity and idiomatic structure."),
    ("fast", "Optimize for raw runtime. Clever implementations are acceptable if they are materially faster."),
    ("low_mem", "Optimize for lower peak memory and minimal auxiliary allocations."),
]


def _extract_rationale_and_code(text: str) -> tuple[str | None, str]:
    lines = text.strip().splitlines()
    if not lines:
        return None, ""
    if lines[0].lower().startswith("rationale:"):
        rationale = lines[0].split(":", 1)[1].strip() or None
        return rationale, _strip_code_fence("\n".join(lines[1:]).strip())
    return None, _strip_code_fence(text.strip())


def _strip_code_fence(text: str) -> str:
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return text


class BenchPreviewService:
    def __init__(self, llm_client: object):
        self.llm_client = llm_client

    async def generate_candidates(
        self,
        *,
        function_name: str,
        language: str,
        agent_code: str,
        surrounding_context: str,
        conversation_history: list[ConversationMessage],
        repo_context: RepoContextConfig | None,
    ) -> BenchRunPreviewResponse:
        repo_snapshot = ""
        context_summary: dict[str, object] = {
            "conversation_messages_stored": len(conversation_history),
            "conversation_messages_sent": 0,
            "repo_context_included": False,
        }
        if repo_context is not None:
            repo_snapshot, repo_summary = build_repo_context(repo_context)
            context_summary.update(repo_summary)
            context_summary["repo_context_included"] = bool(repo_snapshot)

        tasks = [
            self._generate_single_candidate(
                slot=slot,
                objective=objective,
                function_name=function_name,
                language=language,
                agent_code=agent_code,
                surrounding_context=surrounding_context,
                conversation_history=conversation_history,
                repo_snapshot=repo_snapshot,
            )
            for slot, objective in OBJECTIVES
        ]
        generated = await asyncio.gather(*tasks)
        candidates = [
            CandidatePreview(
                slot="agent",
                objective="Original implementation from the coding agent.",
                model=getattr(self.llm_client.settings, "gemini_model", None)
                or getattr(self.llm_client.settings, "cerebras_model", "unknown"),
                rationale="Seed candidate used as slot 0.",
                code=agent_code,
            ),
            *generated,
        ]
        return BenchRunPreviewResponse(
            function_name=function_name,
            model=getattr(self.llm_client.settings, "gemini_model", None)
            or getattr(self.llm_client.settings, "cerebras_model", "unknown"),
            candidates=candidates,
            context_summary=context_summary,
        )

    async def _generate_single_candidate(
        self,
        *,
        slot: str,
        objective: str,
        function_name: str,
        language: str,
        agent_code: str,
        surrounding_context: str,
        conversation_history: list[ConversationMessage],
        repo_snapshot: str,
    ) -> CandidatePreview:
        system_prompt = (
            "You are generating one candidate implementation for a benchmark tournament.\n"
            "You must preserve compatibility with the surrounding codebase and use the provided repository context.\n"
            "Return exactly:\n"
            "Rationale: <one short line>\n"
            "<full function only>\n"
        )
        user_prompt = (
            "An AI coding agent already wrote one implementation of this function.\n"
            "Generate one genuinely different candidate for side-by-side measurement.\n"
            f"Assigned slot: {slot}\n"
            f"Objective: {objective}\n\n"
            f"Language: {language}\n"
            f"Function name: {function_name}\n\n"
            "Agent implementation:\n"
            f"{agent_code}\n\n"
            "Surrounding context:\n"
            f"{surrounding_context}\n"
        )
        if repo_snapshot:
            user_prompt += f"\nRepository context:\n{repo_snapshot}\n"

        messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
        messages.append({"role": "user", "content": user_prompt})
        response: TextGenerationResult = await self.llm_client.chat(
            messages,
            max_completion_tokens=1200,
            temperature=0.7,
        )
        rationale, code = _extract_rationale_and_code(response.text or "")
        return CandidatePreview(
            slot=slot,
            objective=objective,
            model=response.model,
            rationale=rationale,
            code=code,
        )
