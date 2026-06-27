from pydantic import BaseModel, Field, ConfigDict


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


class EditorContext(BaseModel):
    active_file: str | None = None
    selection: str = ""
    visible_files: list[str] = Field(default_factory=list)
    symbol_name: str | None = None


class WorkspaceContext(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    active_file_name: str | None = Field(default=None, alias="activeFileName")
    language_id: str | None = Field(default=None, alias="languageId")
    selected_text: str | None = Field(default=None, alias="selectedText")
    visible_text: str | None = Field(default=None, alias="visibleText")


class FeatureOptionsRequest(BaseModel):
    prompt: str = Field(min_length=1)
    language: str = "typescript"
    active_file_name: str | None = None
    selected_text: str | None = None
    visible_text: str | None = None
    repo_context: RepoContextConfig | None = None


class BenchMetricSet(BaseModel):
    readability: int
    simplicity: int
    speed: int
    memory: int
    maintainability: int
    test_confidence: int = Field(alias="testConfidence")

    model_config = ConfigDict(populate_by_name=True)


class FeatureOption(BaseModel):
    id: str
    title: str
    summary: str
    implementation_plan: str = Field(alias="implementationPlan")
    tradeoffs: list[str]
    generated_code: str = Field(alias="generatedCode")
    metrics: BenchMetricSet | None = None

    model_config = ConfigDict(populate_by_name=True)

    @property
    def implementationPlan(self) -> str:
        return self.implementation_plan

    @property
    def generatedCode(self) -> str:
        return self.generated_code


class FeatureOptionsResponse(BaseModel):
    assistant_message: str = Field(alias="assistantMessage")
    context_summary: str = Field(alias="contextSummary")
    context_metadata: dict[str, object] = Field(alias="contextMetadata")
    gemini_model: str = Field(alias="geminiModel")
    cerebras_model: str = Field(alias="cerebrasModel")
    options: list[FeatureOption]

    model_config = ConfigDict(populate_by_name=True)


class BenchRunRequest(BaseModel):
    function_name: str
    language: str = "python"
    agent_code: str = Field(min_length=1)
    surrounding_context: str = ""
    conversation_history: list[ConversationMessage] = Field(default_factory=list)
    repo_context: RepoContextConfig | None = None
    editor_context: EditorContext | None = None


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


class BenchSuggestion(BaseModel):
    id: str
    title: str
    summary: str
    implementation_plan: str = Field(alias="implementationPlan")
    tradeoffs: list[str]
    generated_code: str = Field(alias="generatedCode")

    model_config = ConfigDict(populate_by_name=True)


class BenchSuggestionsRequest(BaseModel):
    feature_request: str = Field(alias="featureRequest")
    workspace_context: WorkspaceContext = Field(default_factory=WorkspaceContext, alias="workspaceContext")
    repo_context: RepoContextConfig | None = None

    model_config = ConfigDict(populate_by_name=True)


class BenchSuggestionsResponse(BaseModel):
    suggestions: list[BenchSuggestion]
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
    editor_context: EditorContext | None = None
    intent_hint: str = "auto"
    language: str = "python"


class ThreadBenchPreviewRequest(BaseModel):
    function_name: str
    language: str = "python"
    agent_code: str = Field(min_length=1)
    surrounding_context: str = ""
    repo_context: RepoContextConfig | None = None
    editor_context: EditorContext | None = None
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
