from __future__ import annotations

import json
import re
from typing import Any

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


class FeatureOptionsError(ValueError):
    """Raised when the feature-options model response cannot be used."""


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
        try:
            suggestions = _parse_options(response.text or "")
        except FeatureOptionsError:
            raise
        except ValueError as exc:
            raise FeatureOptionsError("Cerebras response did not include usable feature options.") from exc
        return FeatureOptionsResponse(
            assistant_message=_build_assistant_message(len(suggestions)),
            context_summary=context_summary,
            context_metadata=context_metadata,
            gemini_model="local-context-only",
            cerebras_model=response.model,
            options=suggestions,
        )


def _parse_options(content: str) -> list[FeatureOption]:
    payload = _parse_json_object(content)
    raw_suggestions = _option_list_from_payload(payload)
    if not isinstance(raw_suggestions, list) or not raw_suggestions:
        raise FeatureOptionsError("Cerebras response did not contain a suggestions array.")

    options: list[FeatureOption] = []
    for index, item in enumerate(raw_suggestions[:4]):
        if not isinstance(item, dict):
            continue
        raw_tradeoffs = item.get("tradeoffs")
        tradeoffs = raw_tradeoffs if isinstance(raw_tradeoffs, list) else []
        normalized = {
            "id": item.get("id") or f"option-{index + 1}",
            "title": item.get("title") or f"Option {index + 1}",
            "summary": item.get("summary") or "",
            "implementationPlan": item.get("implementationPlan") or item.get("implementation_plan") or "",
            "tradeoffs": [str(tradeoff) for tradeoff in tradeoffs if str(tradeoff).strip()],
            "generatedCode": item.get("generatedCode") or item.get("generated_code") or "",
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
        raise FeatureOptionsError("Cerebras returned no usable implementation options.")
    return options


def _parse_json_object(content: str) -> dict[str, Any]:
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned, flags=re.IGNORECASE).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        json_text = _extract_first_json_object(cleaned)
        if json_text is None:
            raise FeatureOptionsError("Cerebras response was not valid JSON.")
        payload = json.loads(json_text)
    if not isinstance(payload, dict):
        raise FeatureOptionsError("Cerebras response was not a JSON object.")
    return payload


def _extract_first_json_object(text: str) -> str | None:
    start = text.find("{")
    if start == -1:
        return None

    depth = 0
    in_string = False
    escape = False
    for index, char in enumerate(text[start:], start=start):
        if escape:
            escape = False
            continue
        if char == "\\":
            escape = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def _option_list_from_payload(payload: dict[str, Any]) -> Any:
    for key in ("suggestions", "options", "featureOptions", "feature_options", "plans"):
        raw_options = payload.get(key)
        if isinstance(raw_options, list):
            return raw_options

    for key in ("response", "result", "data"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            raw_options = _option_list_from_payload(nested)
            if isinstance(raw_options, list):
                return raw_options

    return payload.get("suggestions")


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
                    "def fibonacci_iterative(n: int) -> int:\n"
                    "    if n < 0:\n"
                    "        raise ValueError('n must be non-negative')\n"
                    "    if n < 2:\n"
                    "        return n\n"
                    "    prev_num, curr_num = 0, 1\n"
                    "    for _ in range(2, n + 1):\n"
                    "        prev_num, curr_num = curr_num, prev_num + curr_num\n"
                    "    return curr_num\n"
                ),
            },
            {
                "id": "fibonacci-memoized",
                "title": "Memoized recursive Fibonacci",
                "summary": "Recursive shape with memoization for repeated subproblems.",
                "implementationPlan": "Implement a recursive helper with a memo dict seeded with base cases.",
                "tradeoffs": ["Nice conceptual mapping", "More overhead than iterative for single calls"],
                "generatedCode": (
                    "def fibonacci_memoized(n: int, memo: dict[int, int] | None = None) -> int:\n"
                    "    if n < 0:\n"
                    "        raise ValueError('n must be non-negative')\n"
                    "    if memo is None:\n"
                    "        memo = {0: 0, 1: 1}\n"
                    "    if n not in memo:\n"
                    "        memo[n] = fibonacci_memoized(n - 1, memo) + fibonacci_memoized(n - 2, memo)\n"
                    "    return memo[n]\n"
                ),
            },
            {
                "id": "fibonacci-fast-doubling",
                "title": "Fast-doubling Fibonacci",
                "summary": "More advanced implementation optimized for fewer recursive steps.",
                "implementationPlan": "Use the fast-doubling recurrence with a tuple-returning helper.",
                "tradeoffs": ["Fastest for large n", "Harder to understand at a glance"],
                "generatedCode": (
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
                ),
            },
        ]
    else:
        slug = _slugify(prompt)
        raw = [
            {
                "id": f"{slug}-simple",
                "title": "Simple service-first implementation",
                "summary": "Keep the feature small, explicit, and easy to revise after the first user test.",
                "implementationPlan": "Add a focused service function, call it from the relevant route or handler, and keep state changes local.",
                "tradeoffs": ["Fastest to review", "May need another abstraction later"],
                "generatedCode": "// hardcoded test option 1\nexport async function buildFeature() {\n  return { ok: true };\n}\n",
            },
            {
                "id": f"{slug}-modular",
                "title": "Modular provider-based implementation",
                "summary": "Introduce replaceable providers now so future sandbox/apply flows slot in cleanly.",
                "implementationPlan": "Define provider interfaces for the feature and wire an MVP implementation behind them.",
                "tradeoffs": ["More extensible", "More structure up front"],
                "generatedCode": "// hardcoded test option 2\nexport interface FeatureProvider {\n  run(input: string): Promise<unknown>;\n}\n",
            },
            {
                "id": f"{slug}-fast",
                "title": "Fast path with cached state",
                "summary": "Bias toward responsiveness by caching repeated work.",
                "implementationPlan": "Add a small in-memory cache keyed by request context and refresh asynchronously.",
                "tradeoffs": ["Snappier UX", "Needs careful invalidation later"],
                "generatedCode": "// hardcoded test option 3\nconst featureCache = new Map<string, unknown>();\n",
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
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:36] or "feature"


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
