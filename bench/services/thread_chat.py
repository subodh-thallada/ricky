from __future__ import annotations

from bench.schemas import ConversationMessage, RepoContextConfig, RoutedReplyResponse
from bench.services.chat_items import split_response_items
from bench.services.chat_router import ChatRouter
from bench.services.repo_context import build_repo_context


class ThreadChatService:
    def __init__(self, router: ChatRouter):
        self.router = router

    async def reply(
        self,
        *,
        thread_id: str,
        history: list[ConversationMessage],
        prompt: str,
        repo_context: RepoContextConfig | None,
        intent_hint: str,
        language: str,
        backboard_thread_id: str | None = None,
        backboard_assistant_id: str | None = None,
    ) -> RoutedReplyResponse:
        mode = self.router.detect_mode(prompt, intent_hint=intent_hint)
        provider_name, client = self.router.provider_for_mode(mode)

        repo_snapshot = ""
        context_summary: dict[str, object] = {
            "conversation_messages_stored": len(history),
            "conversation_messages_sent": 0,
            "repo_context_included": False,
            "mode": mode,
            "focus_paths": repo_context.focus_paths if repo_context else [],
            "query": repo_context.query if repo_context else "",
        }
        if repo_context is not None:
            repo_snapshot, repo_summary = build_repo_context(repo_context)
            context_summary.update(repo_summary)
            context_summary["repo_context_included"] = bool(repo_snapshot)

        memory_snapshot = await self._recall_memories(
            prompt=prompt,
            backboard_assistant_id=backboard_assistant_id,
            context_summary=context_summary,
        )
        messages = self._build_messages(
            history=history,
            prompt=prompt,
            repo_snapshot=repo_snapshot,
            memory_snapshot=memory_snapshot,
            mode=mode,
            language=language,
        )
        result = await client.chat(
            messages,
            max_completion_tokens=1600 if mode == "code" else 600,
            temperature=0.2 if mode == "code" else 0.1,
        )
        memory_result = await self._remember_turn(
            thread_id=thread_id,
            prompt=prompt,
            response_text=result.text or "",
            backboard_thread_id=backboard_thread_id,
            backboard_assistant_id=backboard_assistant_id,
            context_summary=context_summary,
        )
        items = split_response_items(result.text or "")
        return RoutedReplyResponse(
            thread_id=thread_id,
            provider=provider_name,
            model=result.model,
            mode=mode,
            items=items,
            raw_text=result.text or "",
            context_summary=context_summary,
            backboard_thread_id=memory_result.get("thread_id"),
            backboard_assistant_id=memory_result.get("assistant_id"),
        )

    async def _recall_memories(
        self,
        *,
        prompt: str,
        backboard_assistant_id: str | None,
        context_summary: dict[str, object],
    ) -> str:
        try:
            memories = await self.router.backboard.recall(
                assistant_id=backboard_assistant_id,
                query=prompt,
            )
        except Exception as exc:  # pragma: no cover - network and SDK surface vary
            context_summary["backboard_memory_recall_error"] = str(exc)
            return ""
        context_summary["backboard_memories_recalled"] = len(memories)
        if not memories:
            return ""
        return "\n".join(f"- {memory}" for memory in memories)

    async def _remember_turn(
        self,
        *,
        thread_id: str,
        prompt: str,
        response_text: str,
        backboard_thread_id: str | None,
        backboard_assistant_id: str | None,
        context_summary: dict[str, object],
    ) -> dict[str, str | None]:
        content = "\n".join([
            "Bench chat memory update.",
            f"User request: {prompt}",
            f"Assistant response: {response_text[:2000]}",
        ])
        try:
            result = await self.router.backboard.remember(
                content,
                thread_id=backboard_thread_id,
                assistant_id=backboard_assistant_id,
                metadata={"kind": "thread_reply_turn", "bench_thread_id": thread_id},
            )
        except Exception as exc:  # pragma: no cover - network and SDK surface vary
            context_summary["backboard_memory_error"] = str(exc)
            return {"thread_id": backboard_thread_id, "assistant_id": backboard_assistant_id}
        context_summary["backboard_memory_status"] = result.get("status", "unknown")
        return {
            "thread_id": result.get("thread_id") or backboard_thread_id,
            "assistant_id": result.get("assistant_id") or backboard_assistant_id,
        }

    def _build_messages(
        self,
        *,
        history: list[ConversationMessage],
        prompt: str,
        repo_snapshot: str,
        memory_snapshot: str,
        mode: str,
        language: str,
    ) -> list[dict[str, str]]:
        if mode == "code":
            system = (
                "You are a coding assistant. If you generate code, wrap each code artifact in fenced code blocks.\n"
                "Keep explanations concise and place them outside code fences.\n"
                f"Default language: {language}.\n"
            )
        else:
            system = (
                "You are a concise assistant answering non-code questions.\n"
                "Do not generate code unless the user explicitly asks for code.\n"
            )

        messages: list[dict[str, str]] = [{"role": "system", "content": system}]
        user_content = prompt
        if memory_snapshot:
            user_content += f"\n\nRelevant Backboard memory:\n{memory_snapshot}"
        if repo_snapshot:
            user_content += f"\n\nRelevant repository context:\n{repo_snapshot}"
        messages.append({"role": "user", "content": user_content})
        return messages
