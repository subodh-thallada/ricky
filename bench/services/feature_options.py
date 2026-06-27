from __future__ import annotations

import json
import re

from bench.clients.backboard import BackboardAdapter
from bench.clients.cerebras import CerebrasClient
from bench.schemas import (
    BenchMetricSet,
    EditorContext,
    FeatureOption,
    FeatureOptionsRequest,
    FeatureOptionsResponse,
)
from bench.services.context_inference import infer_repo_context
from bench.services.repo_context import build_repo_context


class FeatureOptionsService:
    def __init__(self, cerebras: CerebrasClient, memory: BackboardAdapter | None = None):
        self.cerebras = cerebras
        self.memory = memory

    async def generate(self, request: FeatureOptionsRequest) -> FeatureOptionsResponse:
        prompt = _strip_provider_keywords(request.prompt)
        test_mode = _is_test_mode(request.prompt)
        inferred_context = infer_repo_context(
            prompt=prompt,
            root_path=request.repo_context.root_path if request.repo_context else ".",
            repo_context=request.repo_context,
            editor_context=_request_to_editor_context(request),
        )
        if not test_mode:
            inferred_context = _expand_repo_context_for_provider(inferred_context)
        repo_snapshot, context_metadata = build_repo_context(inferred_context)
        context_summary = _make_context_summary(context_metadata)
        recalled_memories = await self._recall_memories(request, prompt, context_metadata)

        if test_mode:
            suggestions = _parse_options(_build_test_response_text(prompt))
            memory_result = await self._remember_feature_turn(request, prompt, suggestions, context_metadata)
            return FeatureOptionsResponse(
                assistant_message=_build_assistant_message(len(suggestions)),
                context_summary=context_summary,
                context_metadata=context_metadata,
                gemini_model="disabled-cerebras-only",
                cerebras_model="cerebras-test-stub",
                options=[option.model_dump(by_alias=True) for option in suggestions],
                backboard_thread_id=memory_result.get("thread_id"),
                backboard_assistant_id=memory_result.get("assistant_id"),
            )

        user_payload = {
            "featureRequest": prompt,
            "workspaceContext": {
                "activeFileName": request.active_file_name,
                "languageId": request.language,
                "selectedText": request.selected_text,
                "visibleText": _sanitize_test_markers(request.visible_text),
            },
            "repositoryContext": _sanitize_test_markers(repo_snapshot),
            "backboardMemory": recalled_memories,
            "benchContext": {
                "currentUi": {
                    "cards_show": ["title", "summary"],
                    "details_panel_shows": ["metrics", "code", "implementationPlan", "tradeoffs"],
                    "implement_button_behavior": "loads inline preview into the chosen file before apply",
                },
                "placementExpectation": (
                    "Return code in a shape that helps a local editor heuristic choose the right file "
                    "and replace the right symbol or placeholder."
                ),
                "preferMinimalFileSet": True,
            },
        }
        response, suggestions = await self._generate_suggestions(user_payload)
        memory_result = await self._remember_feature_turn(request, prompt, suggestions, context_metadata)
        return FeatureOptionsResponse(
            assistant_message=_build_assistant_message(len(suggestions)),
            context_summary=context_summary,
            context_metadata=context_metadata,
            gemini_model="disabled-cerebras-only",
            cerebras_model=response.model,
            options=[option.model_dump(by_alias=True) for option in suggestions],
            backboard_thread_id=memory_result.get("thread_id"),
            backboard_assistant_id=memory_result.get("assistant_id"),
        )

    async def _recall_memories(
        self,
        request: FeatureOptionsRequest,
        prompt: str,
        context_metadata: dict[str, object],
    ) -> list[str]:
        if self.memory is None:
            return []
        try:
            memories = await self.memory.recall(
                assistant_id=request.backboard_assistant_id,
                query=prompt,
            )
        except Exception as exc:  # pragma: no cover - network and SDK surface vary
            context_metadata["backboard_memory_recall_error"] = str(exc)
            return []
        context_metadata["backboard_memories_recalled"] = len(memories)
        return memories

    async def _remember_feature_turn(
        self,
        request: FeatureOptionsRequest,
        prompt: str,
        suggestions: list[FeatureOption],
        context_metadata: dict[str, object],
    ) -> dict[str, str | None]:
        if self.memory is None:
            return {"thread_id": request.backboard_thread_id, "assistant_id": request.backboard_assistant_id}
        try:
            result = await self.memory.remember(
                _build_backboard_memory_content(prompt, suggestions),
                thread_id=request.backboard_thread_id,
                assistant_id=request.backboard_assistant_id,
                metadata={"kind": "feature_options_turn", "option_count": len(suggestions)},
            )
        except Exception as exc:  # pragma: no cover - network and SDK surface vary
            context_metadata["backboard_memory_error"] = str(exc)
            return {"thread_id": request.backboard_thread_id, "assistant_id": request.backboard_assistant_id}
        context_metadata["backboard_memory_status"] = result.get("status", "unknown")
        return {
            "thread_id": result.get("thread_id") or request.backboard_thread_id,
            "assistant_id": result.get("assistant_id") or request.backboard_assistant_id,
        }

    async def _generate_suggestions(
        self,
        user_payload: dict[str, object],
    ) -> tuple[object, list[FeatureOption]]:
        response = await self.cerebras.chat(
            [
                {"role": "system", "content": _build_system_prompt()},
                {"role": "user", "content": json.dumps(user_payload)},
            ],
            max_completion_tokens=2200,
            temperature=0.35,
            response_format={"type": "json_object"},
            disable_reasoning=True,
        )
        suggestions = _parse_options(response.text or "")
        if not _is_raw_fallback(suggestions):
            return response, suggestions

        retry_response = await self.cerebras.chat(
            [
                {
                    "role": "system",
                    "content": _build_system_prompt()
                    + "\nYour previous response was malformed. Return one valid JSON object and nothing else.",
                },
                {"role": "user", "content": json.dumps(user_payload)},
            ],
            max_completion_tokens=2200,
            temperature=0.1,
            response_format={"type": "json_object"},
            disable_reasoning=True,
        )
        return retry_response, _parse_options(retry_response.text or "")


def _parse_options(content: str) -> list[FeatureOption]:
    cleaned = _strip_outer_json_fence(content)
    try:
        payload = _load_options_json(cleaned)
    except ValueError:
        return [_build_raw_code_option(cleaned)]
    raw_suggestions = payload.get("suggestions", payload.get("options", []))
    if not isinstance(raw_suggestions, list):
        return [_build_raw_code_option(cleaned)]

    options: list[FeatureOption] = []
    for index, item in enumerate(raw_suggestions[:3]):
        if not isinstance(item, dict):
            continue
        normalized = {
            "id": item.get("id") or f"option-{index + 1}",
            "title": item.get("title") or f"Option {index + 1}",
            "summary": item.get("summary") or "",
            "implementationPlan": item.get("implementationPlan") or "",
            "tradeoffs": _normalize_tradeoffs(item.get("tradeoffs")),
            "generatedCode": str(item.get("generatedCode") or ""),
        }
        option = FeatureOption.model_validate(
            {
                **normalized,
                "metrics": _build_metrics(
                    normalized["title"],
                    normalized["summary"],
                    index,
                ).model_dump(by_alias=True),
            }
        )
        generated_code = getattr(option, "generated_code", getattr(option, "generatedCode", ""))
        if option.title and generated_code:
            options.append(option)
    if not options:
        return [_build_raw_code_option(cleaned)]
    return options


def _is_raw_fallback(options: list[FeatureOption]) -> bool:
    return len(options) == 1 and options[0].id == "cerebras-draft"


def _strip_outer_json_fence(content: str) -> str:
    cleaned = content.strip()
    match = re.match(r"^```(?:json)?\s*([\s\S]*?)\s*```$", cleaned, flags=re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return cleaned


def _load_options_json(cleaned: str) -> dict[str, object]:
    payload: object | None = None
    for candidate in (cleaned, _extract_json_object(cleaned), _escape_control_chars_inside_strings(cleaned)):
        if not candidate:
            continue
        try:
            payload = json.loads(candidate)
            break
        except json.JSONDecodeError:
            continue

    if payload is None:
        recovered = _recover_complete_suggestions(cleaned)
        if not recovered:
            raise ValueError("Cerebras response was not valid JSON.")
        payload = {"suggestions": recovered}

    if not isinstance(payload, dict):
        raise ValueError("Cerebras response was not a JSON object.")
    return payload


def _extract_json_object(content: str) -> str | None:
    start = content.find("{")
    end = content.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    return content[start : end + 1].strip()


def _recover_complete_suggestions(content: str) -> list[dict[str, object]]:
    """Salvage complete options when a provider response is truncated."""
    anchor = content.find('"suggestions"')
    bracket = content.find("[", anchor) if anchor != -1 else -1
    if bracket == -1:
        return []

    objects: list[dict[str, object]] = []
    depth = 0
    object_start = -1
    in_string = False
    escaped = False
    for index in range(bracket + 1, len(content)):
        char = content[index]
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = in_string
            continue
        if char == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if char == "{":
            if depth == 0:
                object_start = index
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0 and object_start != -1:
                fragment = content[object_start : index + 1]
                try:
                    item = json.loads(_escape_control_chars_inside_strings(fragment))
                    if isinstance(item, dict):
                        objects.append(item)
                except json.JSONDecodeError:
                    pass
                object_start = -1
        elif char == "]" and depth == 0:
            break
    return objects


def _escape_control_chars_inside_strings(content: str) -> str:
    output: list[str] = []
    in_string = False
    escaped = False
    for char in content:
        if escaped:
            output.append(char)
            escaped = False
            continue
        if char == "\\":
            output.append(char)
            escaped = in_string
            continue
        if char == '"':
            in_string = not in_string
            output.append(char)
            continue
        if in_string and char == "\n":
            output.append("\\n")
        elif in_string and char == "\r":
            output.append("\\r")
        elif in_string and char == "\t":
            output.append("\\t")
        else:
            output.append(char)
    return "".join(output)


def _build_raw_code_option(content: str) -> FeatureOption:
    return FeatureOption.model_validate(
        {
            "id": "cerebras-draft",
            "title": "Cerebras draft",
            "summary": "Use the direct code-shaped response from Cerebras when it did not return structured JSON.",
            "implementationPlan": "Review the generated code in the details pane, then preview it in the editor before accepting.",
            "tradeoffs": [
                "Avoids dropping a useful real response",
                "May have less structured comparison data than a fully formatted response",
            ],
            "generatedCode": content.strip(),
            "metrics": _build_metrics("Cerebras draft", content[:160], 0).model_dump(by_alias=True),
        }
    )


def _normalize_tradeoffs(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _build_system_prompt() -> str:
    return (
        "You are Bench, a VS Code coding assistant.\n"
        "Return between 1 and 3 genuinely different implementation options. Never return more than 3.\n"
        "Prefer 2 distinct, strong solutions when there are meaningful design tradeoffs a developer may want to compare.\n"
        "Return 3 only when the third option adds another materially useful approach.\n"
        "Return exactly 1 when one implementation is clearly strongest or additional options would be artificial, redundant, or weaker.\n"
        "Each option must include a concise title, summary, implementationPlan, tradeoffs, and generatedCode.\n"
        "Bench UI contract:\n"
        "- title: short and distinct.\n"
        "- summary: one brief sentence focused on what makes this option meaningfully different from the other correct options.\n"
        "- implementationPlan: 2 to 4 sentences with enough detail for the View Details panel.\n"
        "- tradeoffs: 2 to 4 crisp bullets, including both upsides and downsides.\n"
        "- generatedCode: production-style code, not pseudocode.\n"
        "When you generate code, prefer file-aware output inside generatedCode using one or more sections in this format:\n"
        "### relative/path.ext\n```language\n...code...\n```\n"
        "Use workspace-relative paths only. If you truly cannot infer a file path, return a single code snippet only.\n"
        "You may create a new file or return changes across multiple files when that is the cleanest implementation. If you do, always use explicit workspace-relative paths for every file.\n"
        "Placement rules for generatedCode:\n"
        "- Prefer the smallest useful set of files; one focused file is best when it is enough, but multi-file output is allowed when needed.\n"
        "- If a focused file or active file already appears to contain the target function/class/placeholder, align the code to that file and that symbol.\n"
        "- If you define a function or class, use the real intended symbol name so Bench can match and replace the correct block locally.\n"
        "- If you are changing a stub, replace the stub with the final implementation instead of returning surrounding commentary.\n"
        "- Do not include explanations inside generatedCode.\n"
        "- Keep imports and helper functions that are actually needed by the implementation.\n"
        "Option design rules:\n"
        "- All options should be correct, but each should reflect a real design choice the user may want to compare.\n"
        "- Avoid returning trivial variants that only rename variables or reorder code.\n"
        "- Make the differences legible at the card-summary level and concrete in the plan/tradeoffs.\n"
        "- Quality and meaningful distinction matter more than filling every option slot.\n"
        "Return only valid JSON. Do not wrap the response in Markdown. Do not include commentary outside JSON.\n"
        'JSON shape: {"suggestions":[{"id":"stable-kebab-id","title":"...","summary":"...",'
        '"implementationPlan":"...","tradeoffs":["..."],"generatedCode":"..."}]}'
    )


def _build_metrics(title: str, summary: str, index: int) -> BenchMetricSet:
    seed = _hash(f"{title}:{summary}:{index}")
    metrics = {
        "readability": 56 + ((seed + index * 17 + 0 * 23) % 39),
        "simplicity": 56 + ((seed + index * 17 + 1 * 23) % 39),
        "speed": 56 + ((seed + index * 17 + 2 * 23) % 39),
        "memory": 56 + ((seed + index * 17 + 3 * 23) % 39),
        "maintainability": 56 + ((seed + index * 17 + 4 * 23) % 39),
        "testConfidence": 56 + ((seed + index * 17 + 5 * 23) % 39),
    }
    combined = f"{title} {summary}"
    if any(term in combined.lower() for term in ["simple", "read", "idiom", "maintain"]):
        metrics["readability"] = min(96, metrics["readability"] + 10)
        metrics["simplicity"] = min(96, metrics["simplicity"] + 8)
    if any(term in combined.lower() for term in ["fast", "performance", "cache", "batch", "parallel"]):
        metrics["speed"] = min(97, metrics["speed"] + 12)
        metrics["memory"] = max(48, metrics["memory"] - 7)
    return BenchMetricSet.model_validate(metrics)


def _hash(value: str) -> int:
    out = 0
    for char in value:
        out = (out * 31 + ord(char)) & 0xFFFFFFFF
    return out


def _make_context_summary(metadata: dict[str, object]) -> str:
    files = metadata.get("files_included", 0)
    strategy = metadata.get("strategy", "unknown")
    cache_hit = metadata.get("cache_hit", False)
    return f"Using {files} summarized files via {strategy}. Cache hit: {cache_hit}."


def _expand_repo_context_for_provider(config: RepoContextConfig) -> RepoContextConfig:
    expanded = config.model_copy(deep=True)
    expanded.max_files = max(expanded.max_files, 28)
    expanded.max_file_chars = max(expanded.max_file_chars, 3500)
    expanded.max_total_chars = max(expanded.max_total_chars, 45000)
    expanded.snippet_context_lines = max(expanded.snippet_context_lines, 12)
    return expanded


def _build_assistant_message(option_count: int) -> str:
    return (
        f"I generated {option_count} implementation options. "
        "Context was condensed locally to limit token usage, and mock metrics are attached for now."
    )


def _is_test_mode(prompt: str) -> bool:
    return "(test)" in prompt.lower()


def _strip_provider_keywords(prompt: str) -> str:
    return prompt.replace("(REAL)", "").replace("(real)", "").replace("(TEST)", "").replace("(test)", "").strip()


def _sanitize_test_markers(value: str | None) -> str | None:
    if value is None:
        return None
    return value.replace("(TEST)", "").replace("(test)", "")


def _build_backboard_memory_content(prompt: str, suggestions: list[FeatureOption]) -> str:
    lines = [
        "Bench chat memory update.",
        f"User request: {prompt}",
        "Generated options:",
    ]
    lines.extend(f"- {option.title}: {option.summary}" for option in suggestions[:3])
    return "\n".join(lines)


def _build_test_response_text(prompt: str) -> str:
    return json.dumps({"suggestions": _build_test_suggestion_dicts(prompt)})


def _build_test_suggestion_dicts(prompt: str) -> list[dict[str, object]]:
    lowered = prompt.lower()
    if "fibonacci" in lowered:
        raw = _fibonacci_test_options()
    elif "enrollment" in lowered and "sync" in lowered or "sync_enrollments" in lowered:
        raw = _enrollment_sync_test_options()
    elif "students" in lowered and "endpoint" in lowered or "get students" in lowered:
        raw = _students_endpoint_test_options()
    elif "cache" in lowered or "caching" in lowered:
        raw = _cache_test_options()
    elif "validation" in lowered or "validate" in lowered:
        raw = _validation_test_options()
    else:
        slug = _slugify(prompt)
        output_path = _default_output_path(slug)
        raw = [
            {
                "id": f"{slug}-simple",
                "title": "Simple service-first implementation",
                "summary": "Keep the feature small, explicit, and easy to revise after the first user test.",
                "implementationPlan": "Add a focused service function, call it from the relevant route or handler, and keep state changes local.",
                "tradeoffs": ["Fastest to review", "May need another abstraction later"],
                "generatedCode": (
                    f"### {output_path}\n"
                    "```ts\n"
                    "// hardcoded test option 1\n"
                    "export async function buildFeature() {\n"
                    "  return { ok: true };\n"
                    "}\n"
                    "```"
                ),
            },
            {
                "id": f"{slug}-modular",
                "title": "Modular provider-based implementation",
                "summary": "Introduce replaceable providers now so future sandbox/apply flows slot in cleanly.",
                "implementationPlan": "Define provider interfaces for the feature and wire an MVP implementation behind them.",
                "tradeoffs": ["More extensible", "More structure up front"],
                "generatedCode": (
                    f"### {output_path}\n"
                    "```ts\n"
                    "// hardcoded test option 2\n"
                    "export interface FeatureProvider {\n"
                    "  run(input: string): Promise<unknown>;\n"
                    "}\n"
                    "```"
                ),
            },
            {
                "id": f"{slug}-fast",
                "title": "Fast path with cached state",
                "summary": "Bias toward responsiveness by caching repeated work.",
                "implementationPlan": "Add a small in-memory cache keyed by request context and refresh asynchronously.",
                "tradeoffs": ["Snappier UX", "Needs careful invalidation later"],
                "generatedCode": (
                    f"### {output_path}\n"
                    "```ts\n"
                    "// hardcoded test option 3\n"
                    "const featureCache = new Map<string, unknown>();\n"
                    "```"
                ),
            },
        ]
    return raw


def _fibonacci_test_options() -> list[dict[str, object]]:
    return [
        {
            "id": "fibonacci-iterative",
            "title": "Readable iterative Fibonacci",
            "summary": "Simple loop-based implementation with explicit state transitions.",
            "implementationPlan": "Add a small helper function with validation and an iterative loop.",
            "tradeoffs": ["Very readable", "Not the asymptotically fastest possible approach"],
            "generatedCode": (
                "### src/fibonacci_demo.py\n"
                "```python\n"
                "def fibonacci(n: int) -> int:\n"
                "    if n < 0:\n"
                "        raise ValueError('n must be non-negative')\n"
                "    if n < 2:\n"
                "        return n\n"
                "    prev_num, curr_num = 0, 1\n"
                "    for _ in range(2, n + 1):\n"
                "        prev_num, curr_num = curr_num, prev_num + curr_num\n"
                "    return curr_num\n"
                "```"
            ),
        },
        {
            "id": "fibonacci-memoized",
            "title": "Memoized recursive Fibonacci",
            "summary": "Recursive shape with memoization for repeated subproblems.",
            "implementationPlan": "Implement a recursive helper with a memo dict seeded with base cases.",
            "tradeoffs": ["Nice conceptual mapping", "More overhead than iterative for single calls"],
            "generatedCode": (
                "### src/fibonacci_demo.py\n"
                "```python\n"
                "def fibonacci(n: int, memo: dict[int, int] | None = None) -> int:\n"
                "    if n < 0:\n"
                "        raise ValueError('n must be non-negative')\n"
                "    if memo is None:\n"
                "        memo = {0: 0, 1: 1}\n"
                "    if n not in memo:\n"
                "        memo[n] = fibonacci(n - 1, memo) + fibonacci(n - 2, memo)\n"
                "    return memo[n]\n"
                "```"
            ),
        },
        {
            "id": "fibonacci-fast-doubling",
            "title": "Fast-doubling Fibonacci",
            "summary": "More advanced implementation optimized for fewer recursive steps.",
            "implementationPlan": "Use the fast-doubling recurrence with a tuple-returning helper.",
            "tradeoffs": ["Fastest for large n", "Harder to understand at a glance"],
            "generatedCode": (
                "### src/fibonacci_demo.py\n"
                "```python\n"
                "def fibonacci(n: int) -> int:\n"
                "    if n < 0:\n"
                "        raise ValueError('n must be non-negative')\n"
                "    def _fib(k: int) -> tuple[int, int]:\n"
                "        if k == 0:\n"
                "            return 0, 1\n"
                "        a, b = _fib(k >> 1)\n"
                "        c = a * ((b << 1) - a)\n"
                "        d = a * a + b * b\n"
                "        return (d, c + d) if (k & 1) else (c, d)\n"
                "    return _fib(n)[0]\n"
                "```"
            ),
        },
    ]


def _students_endpoint_test_options() -> list[dict[str, object]]:
    return [
        {
            "id": "students-endpoint-direct",
            "title": "Direct route with inline filtering",
            "summary": "Keep the endpoint self-contained with straightforward query-parameter handling.",
            "implementationPlan": "Implement filtering, limit handling, and response shaping directly in the route function.",
            "tradeoffs": ["Fastest to ship", "Logic can grow crowded as endpoint behavior expands"],
            "generatedCode": (
                "### src/get_students_demo.py\n"
                "```python\n"
                "from dataclasses import asdict\n"
                "\n"
                "def get_students(search: str | None = None, grade: int | None = None, limit: int = 50) -> list[dict[str, object]]:\n"
                "    items = STUDENTS\n"
                "    if search:\n"
                "        lowered = search.lower()\n"
                "        items = [student for student in items if lowered in student.name.lower()]\n"
                "    if grade is not None:\n"
                "        items = [student for student in items if student.grade == grade]\n"
                "    return [asdict(student) for student in items[:limit]]\n"
                "```"
            ),
        },
        {
            "id": "students-endpoint-service",
            "title": "Service-layer endpoint",
            "summary": "Move query behavior into a dedicated service function so the route stays thin.",
            "implementationPlan": "Add a small query service helper and keep the route focused on HTTP input/output concerns.",
            "tradeoffs": ["Cleaner separation", "Slightly more indirection for a small app"],
            "generatedCode": (
                "### src/get_students_demo.py\n"
                "```python\n"
                "from dataclasses import asdict\n"
                "\n"
                "def query_students(search: str | None, grade: int | None, limit: int) -> list[Student]:\n"
                "    results = STUDENTS\n"
                "    if search:\n"
                "        lowered = search.lower()\n"
                "        results = [student for student in results if lowered in student.name.lower()]\n"
                "    if grade is not None:\n"
                "        results = [student for student in results if student.grade == grade]\n"
                "    return results[:limit]\n"
                "\n"
                "def get_students(search: str | None = None, grade: int | None = None, limit: int = 50) -> list[dict[str, object]]:\n"
                "    return [asdict(student) for student in query_students(search, grade, limit)]\n"
                "```"
            ),
        },
        {
            "id": "students-endpoint-spec",
            "title": "Specification-style endpoint",
            "summary": "Compose reusable predicate functions so future filters can be added without rewriting the route.",
            "implementationPlan": "Build a list of predicate callables, apply them to the dataset, and serialize the filtered result.",
            "tradeoffs": ["Best for growing filter sets", "More abstract than most small demos need"],
            "generatedCode": (
                "### src/get_students_demo.py\n"
                "```python\n"
                "from dataclasses import asdict\n"
                "from typing import Callable\n"
                "\n"
                "def get_students(search: str | None = None, grade: int | None = None, limit: int = 50) -> list[dict[str, object]]:\n"
                "    predicates: list[Callable[[Student], bool]] = []\n"
                "    if search:\n"
                "        lowered = search.lower()\n"
                "        predicates.append(lambda student: lowered in student.name.lower())\n"
                "    if grade is not None:\n"
                "        predicates.append(lambda student: student.grade == grade)\n"
                "\n"
                "    results = [student for student in STUDENTS if all(predicate(student) for predicate in predicates)]\n"
                "    return [asdict(student) for student in results[:limit]]\n"
                "```"
            ),
        },
    ]


def _enrollment_sync_test_options() -> list[dict[str, object]]:
    return [
        {
            "id": "enrollment-sync-dict-index",
            "title": "Dictionary-index sync",
            "summary": "Build student-id indexes first, then do one clear reconciliation pass for updates, additions, and archives.",
            "implementationPlan": "Create indexed maps for existing and incoming records, compare normalized snapshots, then assemble the SyncResult in explicit phases.",
            "tradeoffs": ["Very readable for backend work", "Uses extra temporary maps"],
            "generatedCode": (
                "### src/student_enrollment_sync_demo.py\n"
                "```python\n"
                "def sync_enrollments(\n"
                "    existing: list[EnrollmentRecord],\n"
                "    incoming: list[EnrollmentRecord],\n"
                "    archive_missing: bool = False,\n"
                ") -> SyncResult:\n"
                "    existing_by_id = {record.student_id: record for record in existing}\n"
                "    incoming_by_id = {record.student_id: record for record in incoming}\n"
                "\n"
                "    active: list[EnrollmentRecord] = []\n"
                "    added_ids: list[int] = []\n"
                "    updated_ids: list[int] = []\n"
                "    archived_ids: list[int] = []\n"
                "\n"
                "    for student_id, incoming_record in incoming_by_id.items():\n"
                "        normalized = EnrollmentRecord(\n"
                "            student_id=incoming_record.student_id,\n"
                "            section=incoming_record.section.strip(),\n"
                "            status=incoming_record.status.strip().lower(),\n"
                "            advisor=incoming_record.advisor.strip(),\n"
                "            tags=normalize_tags(incoming_record.tags),\n"
                "        )\n"
                "        existing_record = existing_by_id.get(student_id)\n"
                "        if existing_record is None:\n"
                "            added_ids.append(student_id)\n"
                "            active.append(normalized)\n"
                "            continue\n"
                "        if snapshot_record(existing_record) != snapshot_record(normalized):\n"
                "            updated_ids.append(student_id)\n"
                "        active.append(normalized)\n"
                "\n"
                "    if archive_missing:\n"
                "        for student_id in existing_by_id:\n"
                "            if student_id not in incoming_by_id:\n"
                "                archived_ids.append(student_id)\n"
                "\n"
                "    return SyncResult(active=active, added_ids=added_ids, updated_ids=updated_ids, archived_ids=archived_ids)\n"
                "```"
            ),
        },
        {
            "id": "enrollment-sync-ordered-pass",
            "title": "Ordered pass with carry-forward records",
            "summary": "Preserve incoming order and handle reconciliation in a single pass, then optionally archive leftovers.",
            "implementationPlan": "Track unmatched existing records in a mutable map, walk the incoming list once, and archive what remains only if requested.",
            "tradeoffs": ["Good when output order matters", "Slightly more stateful to read"],
            "generatedCode": (
                "### src/student_enrollment_sync_demo.py\n"
                "```python\n"
                "def sync_enrollments(\n"
                "    existing: list[EnrollmentRecord],\n"
                "    incoming: list[EnrollmentRecord],\n"
                "    archive_missing: bool = False,\n"
                ") -> SyncResult:\n"
                "    remaining_existing = {record.student_id: record for record in existing}\n"
                "    active: list[EnrollmentRecord] = []\n"
                "    added_ids: list[int] = []\n"
                "    updated_ids: list[int] = []\n"
                "\n"
                "    for incoming_record in incoming:\n"
                "        normalized = EnrollmentRecord(\n"
                "            student_id=incoming_record.student_id,\n"
                "            section=incoming_record.section.strip(),\n"
                "            status=incoming_record.status.strip().lower(),\n"
                "            advisor=incoming_record.advisor.strip(),\n"
                "            tags=normalize_tags(incoming_record.tags),\n"
                "        )\n"
                "        previous = remaining_existing.pop(normalized.student_id, None)\n"
                "        if previous is None:\n"
                "            added_ids.append(normalized.student_id)\n"
                "        elif snapshot_record(previous) != snapshot_record(normalized):\n"
                "            updated_ids.append(normalized.student_id)\n"
                "        active.append(normalized)\n"
                "\n"
                "    archived_ids = sorted(remaining_existing) if archive_missing else []\n"
                "    return SyncResult(active=active, added_ids=added_ids, updated_ids=updated_ids, archived_ids=archived_ids)\n"
                "```"
            ),
        },
        {
            "id": "enrollment-sync-helper-driven",
            "title": "Helper-driven sync with normalization stage",
            "summary": "Split normalization from reconciliation so the workflow is easier to test in smaller pieces.",
            "implementationPlan": "Normalize incoming data first, then reconcile ids and snapshots in a compact second phase using helper closures.",
            "tradeoffs": ["Best for testability and reuse", "Adds a bit more indirection"],
            "generatedCode": (
                "### src/student_enrollment_sync_demo.py\n"
                "```python\n"
                "def sync_enrollments(\n"
                "    existing: list[EnrollmentRecord],\n"
                "    incoming: list[EnrollmentRecord],\n"
                "    archive_missing: bool = False,\n"
                ") -> SyncResult:\n"
                "    def normalize(record: EnrollmentRecord) -> EnrollmentRecord:\n"
                "        return EnrollmentRecord(\n"
                "            student_id=record.student_id,\n"
                "            section=record.section.strip(),\n"
                "            status=record.status.strip().lower(),\n"
                "            advisor=record.advisor.strip(),\n"
                "            tags=normalize_tags(record.tags),\n"
                "        )\n"
                "\n"
                "    normalized_existing = {record.student_id: normalize(record) for record in existing}\n"
                "    normalized_incoming = [normalize(record) for record in incoming]\n"
                "\n"
                "    added_ids = [record.student_id for record in normalized_incoming if record.student_id not in normalized_existing]\n"
                "    updated_ids = [\n"
                "        record.student_id\n"
                "        for record in normalized_incoming\n"
                "        if record.student_id in normalized_existing\n"
                "        and snapshot_record(normalized_existing[record.student_id]) != snapshot_record(record)\n"
                "    ]\n"
                "    incoming_ids = {record.student_id for record in normalized_incoming}\n"
                "    archived_ids = sorted(student_id for student_id in normalized_existing if student_id not in incoming_ids) if archive_missing else []\n"
                "\n"
                "    return SyncResult(\n"
                "        active=normalized_incoming,\n"
                "        added_ids=added_ids,\n"
                "        updated_ids=updated_ids,\n"
                "        archived_ids=archived_ids,\n"
                "    )\n"
                "```"
            ),
        },
    ]


def _cache_test_options() -> list[dict[str, object]]:
    return [
        {
            "id": "cache-dict",
            "title": "Manual dictionary cache",
            "summary": "Use an explicit module-level cache for maximum clarity and debugging control.",
            "implementationPlan": "Add a dict keyed by user id and populate it only on cache miss.",
            "tradeoffs": ["Very explicit", "You manage eviction and invalidation yourself"],
            "generatedCode": (
                "### src/cache_profiles_demo.py\n"
                "```python\n"
                "_PROFILE_CACHE: dict[int, dict[str, object]] = {}\n"
                "\n"
                "def get_user_profile(user_id: int) -> dict[str, object]:\n"
                "    if user_id in _PROFILE_CACHE:\n"
                "        return _PROFILE_CACHE[user_id]\n"
                "    profile = fetch_profile_from_source(user_id)\n"
                "    _PROFILE_CACHE[user_id] = profile\n"
                "    return profile\n"
                "```"
            ),
        },
        {
            "id": "cache-lru",
            "title": "Decorator-based LRU cache",
            "summary": "Lean on the standard library for a compact and correct memoization approach.",
            "implementationPlan": "Wrap the profile fetch function with functools.lru_cache and return immutable-ish payloads.",
            "tradeoffs": ["Tiny implementation", "Less control over per-entry invalidation"],
            "generatedCode": (
                "### src/cache_profiles_demo.py\n"
                "```python\n"
                "from functools import lru_cache\n"
                "\n"
                "@lru_cache(maxsize=128)\n"
                "def get_user_profile(user_id: int) -> dict[str, object]:\n"
                "    return fetch_profile_from_source(user_id)\n"
                "```"
            ),
        },
        {
            "id": "cache-stale-aware",
            "title": "Timestamp-aware cache entries",
            "summary": "Store values with freshness metadata so you can choose between speed and staleness tolerance.",
            "implementationPlan": "Cache `(timestamp, value)` pairs and refetch when the entry ages out.",
            "tradeoffs": ["Better real-world behavior", "More moving parts than simple memoization"],
            "generatedCode": (
                "### src/cache_profiles_demo.py\n"
                "```python\n"
                "import time\n"
                "\n"
                "_PROFILE_CACHE: dict[int, tuple[float, dict[str, object]]] = {}\n"
                "_TTL_SECONDS = 30.0\n"
                "\n"
                "def get_user_profile(user_id: int) -> dict[str, object]:\n"
                "    cached = _PROFILE_CACHE.get(user_id)\n"
                "    now = time.time()\n"
                "    if cached and now - cached[0] < _TTL_SECONDS:\n"
                "        return cached[1]\n"
                "    profile = fetch_profile_from_source(user_id)\n"
                "    _PROFILE_CACHE[user_id] = (now, profile)\n"
                "    return profile\n"
                "```"
            ),
        },
    ]


def _validation_test_options() -> list[dict[str, object]]:
    return [
        {
            "id": "validation-fail-fast",
            "title": "Fail-fast validation",
            "summary": "Raise the first validation issue immediately for simple and readable control flow.",
            "implementationPlan": "Check each field in order and raise a ValueError as soon as something is invalid.",
            "tradeoffs": ["Simple behavior", "User only sees one issue at a time"],
            "generatedCode": (
                "### src/validation_demo.py\n"
                "```python\n"
                "def validate_student_payload(payload: dict[str, object]) -> dict[str, object]:\n"
                "    name = str(payload.get('name', '')).strip()\n"
                "    if not name:\n"
                "        raise ValueError('name is required')\n"
                "    grade = payload.get('grade')\n"
                "    if not isinstance(grade, int) or grade < 1 or grade > 12:\n"
                "        raise ValueError('grade must be an integer from 1 to 12')\n"
                "    email = str(payload.get('email', '')).strip()\n"
                "    if '@' not in email:\n"
                "        raise ValueError('email must contain @')\n"
                "    return {'name': name, 'grade': grade, 'email': email.lower()}\n"
                "```"
            ),
        },
        {
            "id": "validation-collect-all",
            "title": "Collect-all validation errors",
            "summary": "Return every validation issue at once so the caller can fix the whole payload in one pass.",
            "implementationPlan": "Accumulate field-specific errors in a dict and raise once after all checks complete.",
            "tradeoffs": ["Best UX for forms", "Slightly more code than fail-fast"],
            "generatedCode": (
                "### src/validation_demo.py\n"
                "```python\n"
                "def validate_student_payload(payload: dict[str, object]) -> dict[str, object]:\n"
                "    errors: dict[str, str] = {}\n"
                "    name = str(payload.get('name', '')).strip()\n"
                "    if not name:\n"
                "        errors['name'] = 'name is required'\n"
                "    grade = payload.get('grade')\n"
                "    if not isinstance(grade, int) or not 1 <= grade <= 12:\n"
                "        errors['grade'] = 'grade must be an integer from 1 to 12'\n"
                "    email = str(payload.get('email', '')).strip()\n"
                "    if '@' not in email:\n"
                "        errors['email'] = 'email must contain @'\n"
                "    if errors:\n"
                "        raise ValueError(errors)\n"
                "    return {'name': name, 'grade': grade, 'email': email.lower()}\n"
                "```"
            ),
        },
        {
            "id": "validation-normalize",
            "title": "Normalization-first validation",
            "summary": "Normalize the payload into a clean structure while validating, which works well for downstream code.",
            "implementationPlan": "Create a normalized output dict, sanitize each field, and reject invalid values before returning.",
            "tradeoffs": ["Great downstream ergonomics", "Can feel more indirect during debugging"],
            "generatedCode": (
                "### src/validation_demo.py\n"
                "```python\n"
                "def validate_student_payload(payload: dict[str, object]) -> dict[str, object]:\n"
                "    normalized = {\n"
                "        'name': str(payload.get('name', '')).strip().title(),\n"
                "        'email': str(payload.get('email', '')).strip().lower(),\n"
                "        'grade': payload.get('grade'),\n"
                "    }\n"
                "    if not normalized['name']:\n"
                "        raise ValueError('name is required')\n"
                "    if not isinstance(normalized['grade'], int) or not 1 <= normalized['grade'] <= 12:\n"
                "        raise ValueError('grade must be an integer from 1 to 12')\n"
                "    if '@' not in normalized['email']:\n"
                "        raise ValueError('email must contain @')\n"
                "    return normalized\n"
                "```"
            ),
        },
    ]


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:36] or "feature"


def _default_output_path(slug: str) -> str:
    return f"src/generated/{slug}.ts"


def _request_to_editor_context(request: FeatureOptionsRequest) -> EditorContext:
    visible_files: list[str] = []
    if request.active_file_name:
        visible_files.append(request.active_file_name)
    return EditorContext(
        active_file=request.active_file_name,
        selection=request.selected_text or "",
        visible_files=visible_files,
        symbol_name=None,
    )
