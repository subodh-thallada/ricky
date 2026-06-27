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
    ) -> RoutedReplyResponse:
        mode = self.router.detect_mode(prompt, intent_hint=intent_hint)
        provider_name, client = self.router.provider_for_mode(mode)

        repo_snapshot = ""
        context_summary: dict[str, object] = {
            "conversation_messages": len(history),
            "repo_context_included": False,
            "mode": mode,
        }
        if repo_context is not None:
            repo_snapshot, repo_summary = build_repo_context(repo_context)
            context_summary.update(repo_summary)
            context_summary["repo_context_included"] = bool(repo_snapshot)

        messages = self._build_messages(
            history=history,
            prompt=prompt,
            repo_snapshot=repo_snapshot,
            mode=mode,
            language=language,
        )
        result = await client.chat(
            messages,
            max_completion_tokens=1600 if mode == "code" else 600,
            temperature=0.2 if mode == "code" else 0.1,
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
        )

    def _build_messages(
        self,
        *,
        history: list[ConversationMessage],
        prompt: str,
        repo_snapshot: str,
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
        messages.extend({"role": msg.role, "content": msg.content} for msg in history)

        user_content = prompt
        if repo_snapshot:
            user_content += f"\n\nRelevant repository context:\n{repo_snapshot}"
        messages.append({"role": "user", "content": user_content})
        return messages
