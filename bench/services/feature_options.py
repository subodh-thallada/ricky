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
    def __init__(self, cerebras: CerebrasClient, backboard: BackboardAdapter | None = None):
        self.cerebras = cerebras
        self.backboard = backboard

    async def generate(self, request: FeatureOptionsRequest) -> FeatureOptionsResponse:
        prompt = _strip_provider_keywords(request.prompt)
        real_mode = _is_real_mode(request.prompt)
        inferred_context = infer_repo_context(
            prompt=prompt,
            root_path=request.repo_context.root_path if request.repo_context else ".",
            repo_context=request.repo_context,
            editor_context=_request_to_editor_context(request),
        )
        repo_snapshot, context_metadata = build_repo_context(inferred_context)
        context_summary = _make_context_summary(context_metadata)
        preference_memories = await self._search_preference_memories(prompt)
        context_metadata["preference_memories_included"] = len(preference_memories)

        if not real_mode:
            suggestions = _parse_options(_build_test_response_text(prompt))
            _apply_recommendation(suggestions, preference_memories)
            return FeatureOptionsResponse(
                assistant_message=_build_assistant_message(len(suggestions)),
                context_summary=context_summary,
                context_metadata=context_metadata,
                gemini_model="local-context-only",
                cerebras_model="cerebras-test-stub",
                options=[option.model_dump(by_alias=True) for option in suggestions],
            )

        system_prompt = (
            "You are Bench, a VS Code coding assistant.\n"
            "Given a user's feature request and optional editor context, return up to 3 genuinely different implementation options.\n"
            "If there is one clear best implementation and the alternatives would be low-value or artificial, returning just 1 option is allowed.\n"
            "Prefer multiple options only when they reflect real design choices the user may want to compare.\n"
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
            "Placement rules for generatedCode:\n"
            "- Prefer one focused file when possible so Bench can preview inline.\n"
            "- If a focused file or active file already appears to contain the target function/class/placeholder, align the code to that file and that symbol.\n"
            "- If you define a function or class, use the real intended symbol name so Bench can match and replace the correct block locally.\n"
            "- If you are changing a stub, replace the stub with the final implementation instead of returning surrounding commentary.\n"
            "- Do not include explanations inside generatedCode.\n"
            "- Keep imports and helper functions that are actually needed by the implementation.\n"
            "Option design rules:\n"
            "- All options should be correct, but each should reflect a real design choice the user may want to compare.\n"
            "- Avoid returning trivial variants that only rename variables or reorder code.\n"
            "- Make the differences legible at the card-summary level and concrete in the plan/tradeoffs.\n"
            "- Return at most 3 options.\n"
            "Return only valid JSON. Do not wrap the response in Markdown. Do not include commentary outside JSON.\n"
            'JSON shape: {"suggestions":[{"id":"stable-kebab-id","title":"...","summary":"...",'
            '"implementationPlan":"...","tradeoffs":["..."],"generatedCode":"..."}]}'
        )
        user_payload = {
            "featureRequest": prompt,
            "workspaceContext": {
                "activeFileName": request.active_file_name,
                "languageId": request.language,
                "selectedText": request.selected_text,
                "visibleText": _sanitize_test_markers(request.visible_text) if real_mode else request.visible_text,
            },
            "repositoryContext": _sanitize_test_markers(repo_snapshot) if real_mode else repo_snapshot,
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
                "preferFocusedSingleFile": True,
            },
            "rememberedPreferenceSignals": _memory_texts(preference_memories),
        }
        response = await self.cerebras.chat(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_payload)},
            ],
            max_completion_tokens=2200,
            temperature=0.35,
        )
        suggestions = _parse_options(response.text or "")
        _apply_recommendation(suggestions, preference_memories)
        return FeatureOptionsResponse(
            assistant_message=_build_assistant_message(len(suggestions)),
            context_summary=context_summary,
            context_metadata=context_metadata,
            gemini_model="local-context-only",
            cerebras_model=response.model,
            options=[option.model_dump(by_alias=True) for option in suggestions],
        )

    async def _search_preference_memories(self, prompt: str) -> list[dict[str, object]]:
        if self.backboard is None:
            return []
        query = (
            "Bench implementation preference for this feature request. "
            f"Request: {prompt}"
        )
        return await self.backboard.search_decision_memories(query, limit=8)


def _parse_options(content: str) -> list[FeatureOption]:
    cleaned = content.replace("```json", "").replace("```", "").strip()
    payload = json.loads(cleaned)
    raw_suggestions = payload.get("suggestions", [])
    if not isinstance(raw_suggestions, list):
        raise ValueError("Cerebras response did not contain a suggestions array.")

    options: list[FeatureOption] = []
    for index, item in enumerate(raw_suggestions[:3]):
        normalized = {
            "id": item.get("id") or f"option-{index + 1}",
            "title": item.get("title") or f"Option {index + 1}",
            "summary": item.get("summary") or "",
            "implementationPlan": item.get("implementationPlan") or "",
            "tradeoffs": item.get("tradeoffs") or [],
            "generatedCode": item.get("generatedCode") or "",
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
        raise ValueError("Cerebras returned no usable implementation options.")
    return options


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


def _build_assistant_message(option_count: int) -> str:
    return (
        f"I generated {option_count} implementation options. "
        "Context was condensed locally to limit token usage, and mock metrics are attached for now."
    )


def _is_real_mode(prompt: str) -> bool:
    return "(real)" in prompt.lower()


def _strip_provider_keywords(prompt: str) -> str:
    return prompt.replace("(REAL)", "").replace("(real)", "").replace("(TEST)", "").replace("(test)", "").strip()


def _sanitize_test_markers(value: str | None) -> str | None:
    if value is None:
        return None
    return value.replace("(TEST)", "").replace("(test)", "")


def _build_test_response_text(prompt: str) -> str:
    return json.dumps({"suggestions": _build_test_suggestion_dicts(prompt)})


def _build_test_suggestion_dicts(prompt: str) -> list[dict[str, object]]:
    lowered = prompt.lower()
    if "fibonacci" in lowered:
        raw = _fibonacci_test_options()
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


METRIC_KEYWORDS = {
    "readability": ["readable", "clarity", "clear", "explicit", "understand", "idiom", "maintain"],
    "simplicity": ["simple", "small", "minimal", "straightforward", "focused", "least"],
    "speed": ["fast", "speed", "performance", "runtime", "parallel", "cache", "batch"],
    "memory": ["memory", "space", "allocation", "stream", "efficient"],
    "maintainability": ["maintain", "modular", "extensible", "separation", "service", "provider"],
    "testConfidence": ["test", "confidence", "validated", "edge", "coverage", "safe"],
}


def _apply_recommendation(options: list[FeatureOption], memories: list[dict[str, object]]) -> None:
    for option in options:
        option.recommended = False
        option.recommendationReason = None

    if not options or not memories:
        return

    weights = _preference_weights(memories)
    scored = [(option, _recommendation_score(option, weights, memories)) for option in options]
    best_option, best_score = max(scored, key=lambda item: item[1])
    if best_score <= 0:
        return

    best_option.recommended = True
    top_metrics = sorted(weights.items(), key=lambda item: item[1], reverse=True)[:2]
    if top_metrics:
        labels = ", ".join(_metric_label(metric) for metric, _ in top_metrics)
        best_option.recommendationReason = f"Recommended from prior accepted choices that emphasized {labels}."
    else:
        best_option.recommendationReason = "Recommended from similar previously accepted implementation choices."


def _preference_weights(memories: list[dict[str, object]]) -> dict[str, float]:
    weights = {metric: 0.0 for metric in METRIC_KEYWORDS}
    for text in _memory_texts(memories):
        lowered = text.lower()
        for metric, keywords in METRIC_KEYWORDS.items():
            weights[metric] += sum(1.0 for keyword in keywords if keyword in lowered)
    return weights


def _recommendation_score(
    option: FeatureOption,
    weights: dict[str, float],
    memories: list[dict[str, object]],
) -> float:
    metrics = option.metrics.model_dump(by_alias=True)
    metric_score = sum((metrics.get(metric, 0) / 100.0) * weight for metric, weight in weights.items())
    option_text = f"{option.title} {option.summary} {option.implementationPlan} {' '.join(option.tradeoffs)}".lower()
    keyword_score = 0.0
    for metric, keywords in METRIC_KEYWORDS.items():
        if weights.get(metric, 0) <= 0:
            continue
        keyword_score += sum(0.35 for keyword in keywords if keyword in option_text)

    similarity_score = 0.0
    option_tokens = _signal_tokens(option_text)
    for memory_text in _memory_texts(memories):
        memory_tokens = _signal_tokens(memory_text)
        if option_tokens and memory_tokens:
            similarity_score += len(option_tokens & memory_tokens) / max(1, len(option_tokens | memory_tokens))
    return metric_score + keyword_score + similarity_score


def _memory_texts(memories: list[dict[str, object]]) -> list[str]:
    texts: list[str] = []
    for memory in memories:
        for key in ("content", "text", "memory"):
            value = memory.get(key)
            if isinstance(value, str) and value.strip():
                texts.append(value.strip())
                break
    return texts


def _signal_tokens(text: str) -> set[str]:
    stopwords = {"the", "and", "for", "with", "that", "this", "option", "implementation", "choice", "chosen"}
    return {
        token
        for token in re.findall(r"[a-z][a-z0-9]{3,}", text.lower())
        if token not in stopwords
    }


def _metric_label(metric: str) -> str:
    return "test confidence" if metric == "testConfidence" else metric
