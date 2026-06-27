from __future__ import annotations

import json

from bench.clients.cerebras import CerebrasClient
from bench.schemas import BenchMetricSet, EditorContext, FeatureOption, FeatureOptionsResponse, FeatureOptionsRequest
from bench.services.context_inference import infer_repo_context
from bench.services.repo_context import build_repo_context


class FeatureOptionsService:
    def __init__(self, cerebras: CerebrasClient):
        self.cerebras = cerebras

    async def generate(self, request: FeatureOptionsRequest) -> FeatureOptionsResponse:
        inferred_context = infer_repo_context(
            prompt=request.prompt,
            root_path=request.repo_context.root_path if request.repo_context else ".",
            repo_context=request.repo_context,
            editor_context=_request_to_editor_context(request),
        )
        repo_snapshot, context_metadata = build_repo_context(inferred_context)
        context_summary = _make_context_summary(context_metadata)

        if "(test)" in request.prompt.lower():
            suggestions = _build_test_options(request.prompt)
            return FeatureOptionsResponse(
                assistant_message=_build_assistant_message(len(suggestions)),
                context_summary=context_summary,
                context_metadata=context_metadata,
                gemini_model="local-context-only",
                cerebras_model="cerebras-test-stub",
                options=suggestions,
            )

        system_prompt = (
            "You are Bench, a VS Code coding assistant.\n"
            "Given a user's feature request and optional editor context, return 3 or 4 genuinely different implementation options.\n"
            "Each option must include a concise title, summary, implementationPlan, tradeoffs, and generatedCode.\n"
            "When you generate code, prefer file-aware output inside generatedCode using one or more sections in this format:\n"
            "### relative/path.ext\n```language\n...code...\n```\n"
            "Use workspace-relative paths only. If you truly cannot infer a file path, return a single code snippet only.\n"
            "Return only valid JSON. Do not wrap the response in Markdown. Do not include commentary outside JSON.\n"
            'JSON shape: {"suggestions":[{"id":"stable-kebab-id","title":"...","summary":"...",'
            '"implementationPlan":"...","tradeoffs":["..."],"generatedCode":"..."}]}'
        )
        user_payload = {
            "featureRequest": request.prompt,
            "workspaceContext": {
                "activeFileName": request.active_file_name,
                "languageId": request.language,
                "selectedText": request.selected_text,
                "visibleText": request.visible_text,
            },
            "repositoryContext": repo_snapshot,
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
        return FeatureOptionsResponse(
            assistant_message=_build_assistant_message(len(suggestions)),
            context_summary=context_summary,
            context_metadata=context_metadata,
            gemini_model="local-context-only",
            cerebras_model=response.model,
            options=suggestions,
        )


def _parse_options(content: str) -> list[FeatureOption]:
    cleaned = content.replace("```json", "").replace("```", "").strip()
    payload = json.loads(cleaned)
    raw_suggestions = payload.get("suggestions", [])
    if not isinstance(raw_suggestions, list):
        raise ValueError("Cerebras response did not contain a suggestions array.")

    options: list[FeatureOption] = []
    for index, item in enumerate(raw_suggestions[:4]):
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
        if option.title and option.generated_code:
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


def _build_test_options(prompt: str) -> list[FeatureOption]:
    lowered = prompt.lower()
    if "fibonacci" in lowered:
        raw = [
            {
                "id": "fibonacci-iterative",
                "title": "Readable iterative Fibonacci",
                "summary": "Simple loop-based implementation with explicit state transitions.",
                "implementationPlan": "Add a small helper function with validation and an iterative loop.",
                "tradeoffs": ["Very readable", "Not the asymptotically fastest possible approach"],
                "generatedCode": (
                    "### bench_preview/fibonacci_iterative.py\n"
                    "```python\n"
                    "def fibonacci_iterative(n: int) -> int:\n"
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
                    "### bench_preview/fibonacci_memoized.py\n"
                    "```python\n"
                    "def fibonacci_memoized(n: int, memo: dict[int, int] | None = None) -> int:\n"
                    "    if n < 0:\n"
                    "        raise ValueError('n must be non-negative')\n"
                    "    if memo is None:\n"
                    "        memo = {0: 0, 1: 1}\n"
                    "    if n not in memo:\n"
                    "        memo[n] = fibonacci_memoized(n - 1, memo) + fibonacci_memoized(n - 2, memo)\n"
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
                    "### bench_preview/fibonacci_fast_doubling.py\n"
                    "```python\n"
                    "def fibonacci_fast_doubling(n: int) -> int:\n"
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
    return [
        FeatureOption.model_validate(
            {
                **item,
                "metrics": _build_metrics(item["title"], item["summary"], index).model_dump(by_alias=True),
            }
        )
        for index, item in enumerate(raw)
    ]


def _slugify(text: str) -> str:
    import re

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
