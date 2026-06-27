from pydantic import BaseModel, Field


class ConversationMessage(BaseModel):
    role: str
    content: str = Field(min_length=1)


class ChatItem(BaseModel):
    kind: str
    content: str
    language: str | None = None
    title: str | None = None


class RepoContextConfig(BaseModel):
    root_path: str = "."
    include_extensions: list[str] = Field(
        default_factory=lambda: [
            ".py",
            ".md",
            ".toml",
            ".json",
            ".yml",
            ".yaml",
            ".ts",
            ".tsx",
            ".js",
            ".jsx",
        ]
    )
    focus_paths: list[str] = Field(default_factory=list)
    query: str = ""
    include_file_tree: bool = True
    max_files: int = 12
    max_file_chars: int = 1500
    max_total_chars: int = 12000
    snippet_context_lines: int = 6


class BenchRunRequest(BaseModel):
    function_name: str
    language: str = "python"
    agent_code: str = Field(min_length=1)
    surrounding_context: str = ""
    conversation_history: list[ConversationMessage] = Field(default_factory=list)
    repo_context: RepoContextConfig | None = None


class CandidatePreview(BaseModel):
    slot: str
    objective: str
    model: str
    rationale: str | None = None
    code: str


class BenchRunPreviewResponse(BaseModel):
    function_name: str
    model: str
    candidates: list[CandidatePreview]
    context_summary: dict[str, object]


class ThreadCreateRequest(BaseModel):
    title: str | None = None
    repo_context: RepoContextConfig | None = None


class ThreadMessageCreateRequest(BaseModel):
    role: str
    content: str = Field(min_length=1)


class ThreadReplyRequest(BaseModel):
    prompt: str = Field(min_length=1)
    repo_context: RepoContextConfig | None = None
    intent_hint: str = "auto"
    language: str = "python"


class ThreadBenchPreviewRequest(BaseModel):
    function_name: str
    language: str = "python"
    agent_code: str = Field(min_length=1)
    surrounding_context: str = ""
    repo_context: RepoContextConfig | None = None
    append_messages: list[ConversationMessage] = Field(default_factory=list)


class StoredThread(BaseModel):
    thread_id: str
    title: str | None = None
    messages: list[ConversationMessage] = Field(default_factory=list)
    repo_context: RepoContextConfig | None = None


class RoutedReplyResponse(BaseModel):
    thread_id: str
    provider: str
    model: str
    mode: str
    items: list[ChatItem]
    raw_text: str
    context_summary: dict[str, object]


class FeatureOptionRequest(BaseModel):
    prompt: str = Field(min_length=1)
    language: str = "typescript"
    active_file_name: str | None = None
    selected_text: str | None = None
    visible_text: str | None = None
    repo_context: RepoContextConfig | None = None


class FeatureMetricSet(BaseModel):
    readability: int
    simplicity: int
    speed: int
    memory: int
    maintainability: int
    testConfidence: int


class FeatureOption(BaseModel):
    id: str
    title: str
    summary: str
    implementationPlan: str
    tradeoffs: list[str] = Field(default_factory=list)
    generatedCode: str
    metrics: FeatureMetricSet


class FeatureOptionResponse(BaseModel):
    assistantMessage: str
    contextSummary: str
    contextMetadata: dict[str, object]
    geminiModel: str
    cerebrasModel: str
    options: list[FeatureOption]
