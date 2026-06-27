from fastapi import FastAPI, HTTPException

from bench.clients.anthropic import AnthropicClient
from bench.clients.backboard import BackboardAdapter
from bench.clients.cerebras import CerebrasClient
from bench.config import get_settings
from bench.schemas import (
    BenchRunPreviewResponse,
    BenchRunRequest,
    ConversationMessage,
    FeatureOptionsRequest,
    FeatureOptionsResponse,
    RoutedReplyResponse,
    StoredThread,
    ThreadBenchPreviewRequest,
    ThreadCreateRequest,
    ThreadMessageCreateRequest,
    ThreadReplyRequest,
)
from bench.services.bench_preview import BenchPreviewService
from bench.services.chat_router import ChatRouter
from bench.services.context_inference import infer_repo_context
from bench.services.feature_options import FeatureOptionsService
from bench.services.thread_chat import ThreadChatService
from bench.services.thread_store import ThreadStore

app = FastAPI(title="Bench Orchestrator")
thread_store = ThreadStore()


def _build_llm_client(settings):
    return CerebrasClient(settings)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/providers/check")
async def providers_check() -> dict[str, object]:
    settings = get_settings()
    cerebras = CerebrasClient(settings)
    backboard = BackboardAdapter(settings)
    anthropic = AnthropicClient(settings)
    return {
        "primary_provider": settings.primary_llm_provider,
        "chat_provider": settings.chat_provider,
        "anthropic": await anthropic.check(),
        "cerebras": await cerebras.check(),
        "backboard": await backboard.check(),
    }


@app.post("/feature-options", response_model=FeatureOptionsResponse)
async def feature_options(request: FeatureOptionsRequest) -> FeatureOptionsResponse:
    settings = get_settings()
    service = FeatureOptionsService(
        CerebrasClient(settings),
        AnthropicClient(settings),
        BackboardAdapter(settings),
    )
    return await service.generate(request)


@app.post("/bench/preview", response_model=BenchRunPreviewResponse)
async def bench_preview(request: BenchRunRequest) -> BenchRunPreviewResponse:
    settings = get_settings()
    service = BenchPreviewService(_build_llm_client(settings))
    repo_context = infer_repo_context(
        prompt=f"{request.function_name} {request.surrounding_context}",
        root_path=".",
        repo_context=request.repo_context,
        editor_context=request.editor_context,
    )
    return await service.generate_candidates(
        function_name=request.function_name,
        language=request.language,
        agent_code=request.agent_code,
        surrounding_context=request.surrounding_context,
        conversation_history=request.conversation_history,
        repo_context=repo_context,
    )


@app.post("/threads", response_model=StoredThread)
async def create_thread(request: ThreadCreateRequest) -> StoredThread:
    return thread_store.create_thread(
        title=request.title,
        repo_context=request.repo_context,
    )


@app.get("/threads/{thread_id}", response_model=StoredThread)
async def get_thread(thread_id: str) -> StoredThread:
    thread = thread_store.get_thread(thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="thread_not_found")
    return thread


@app.post("/threads/{thread_id}/messages", response_model=StoredThread)
async def append_thread_message(
    thread_id: str,
    request: ThreadMessageCreateRequest,
) -> StoredThread:
    thread = thread_store.get_thread(thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="thread_not_found")
    return thread_store.append_message(
        thread_id,
        ConversationMessage(role=request.role, content=request.content),
    )


@app.post("/threads/{thread_id}/reply", response_model=RoutedReplyResponse)
async def reply_in_thread(
    thread_id: str,
    request: ThreadReplyRequest,
) -> RoutedReplyResponse:
    thread = thread_store.get_thread(thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="thread_not_found")

    if request.repo_context is not None:
        thread = thread_store.update_repo_context(thread_id, request.repo_context)
    elif thread.repo_context is None:
        inferred = infer_repo_context(
            prompt=request.prompt,
            root_path=".",
            repo_context=None,
            editor_context=request.editor_context,
        )
        thread = thread_store.update_repo_context(thread_id, inferred)

    prior_history = list(thread.messages)
    thread = thread_store.append_message(
        thread_id,
        ConversationMessage(role="user", content=request.prompt),
    )

    settings = get_settings()
    service = ThreadChatService(ChatRouter(settings))
    response = await service.reply(
        thread_id=thread_id,
        history=prior_history,
        prompt=request.prompt,
        repo_context=thread.repo_context,
        intent_hint=request.intent_hint,
        language=request.language,
        backboard_thread_id=thread.backboard_thread_id,
        backboard_assistant_id=thread.backboard_assistant_id,
    )
    thread_store.append_message(
        thread_id,
        ConversationMessage(role="assistant", content=response.raw_text),
    )
    if response.backboard_thread_id or response.backboard_assistant_id:
        thread_store.update_backboard_ids(
            thread_id,
            backboard_thread_id=response.backboard_thread_id,
            backboard_assistant_id=response.backboard_assistant_id,
        )
    return response


@app.post("/threads/{thread_id}/bench-preview", response_model=BenchRunPreviewResponse)
async def bench_preview_for_thread(
    thread_id: str,
    request: ThreadBenchPreviewRequest,
) -> BenchRunPreviewResponse:
    thread = thread_store.get_thread(thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="thread_not_found")

    if request.repo_context is not None:
        thread = thread_store.update_repo_context(thread_id, request.repo_context)
    elif thread.repo_context is None:
        inferred = infer_repo_context(
            prompt=f"{request.function_name} {request.surrounding_context}",
            root_path=".",
            repo_context=None,
            editor_context=request.editor_context,
        )
        thread = thread_store.update_repo_context(thread_id, inferred)

    if request.append_messages:
        thread = thread_store.append_messages(thread_id, request.append_messages)

    settings = get_settings()
    service = BenchPreviewService(_build_llm_client(settings))
    return await service.generate_candidates(
        function_name=request.function_name,
        language=request.language,
        agent_code=request.agent_code,
        surrounding_context=request.surrounding_context,
        conversation_history=thread.messages,
        repo_context=thread.repo_context,
    )
