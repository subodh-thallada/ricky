from __future__ import annotations

import re
from pathlib import Path

from bench.schemas import EditorContext, RepoContextConfig


COMMON_PATH_HINTS: dict[str, list[str]] = {
    "endpoint": ["routes", "route", "api", "controllers", "services", "models", "schemas"],
    "api": ["routes", "route", "api", "controllers", "services", "models", "schemas"],
    "route": ["routes", "route", "api", "controllers", "services"],
    "controller": ["controllers", "services", "models", "schemas"],
    "service": ["services", "models", "schemas"],
    "model": ["models", "schemas", "db"],
    "page": ["pages", "components", "routes", "views"],
    "component": ["components", "pages", "ui"],
}


def infer_repo_context(
    *,
    prompt: str,
    root_path: str = ".",
    repo_context: RepoContextConfig | None,
    editor_context: EditorContext | None,
) -> RepoContextConfig:
    config = repo_context.model_copy(deep=True) if repo_context is not None else RepoContextConfig()
    if config.root_path == ".":
        config.root_path = root_path

    inferred_focus = _infer_focus_paths(prompt=prompt, root_path=config.root_path, editor_context=editor_context)
    inferred_query = _infer_query(prompt=prompt, editor_context=editor_context)

    if not config.focus_paths:
        config.focus_paths = inferred_focus
    else:
        config.focus_paths = _dedupe(config.focus_paths + inferred_focus)

    if not config.query.strip():
        config.query = inferred_query
    else:
        config.query = " ".join(_dedupe((config.query + " " + inferred_query).split()))

    return config


def _infer_focus_paths(
    *,
    prompt: str,
    root_path: str,
    editor_context: EditorContext | None,
) -> list[str]:
    root = Path(root_path).resolve()
    candidates: list[str] = []

    if editor_context is not None:
        path_sources = []
        if editor_context.active_file:
            path_sources.append(editor_context.active_file)
        path_sources.extend(editor_context.visible_files[:6])
        for source in path_sources:
            rel = _normalize_to_repo_relative(root, source)
            if rel is None:
                continue
            parts = Path(rel).parts
            if len(parts) >= 2:
                candidates.append(str(Path(*parts[:2])))
            if len(parts) >= 1:
                candidates.append(parts[0])

    lowered = prompt.lower()
    for keyword, hints in COMMON_PATH_HINTS.items():
        if keyword in lowered:
            candidates.extend(hints)

    return _dedupe(candidates)[:8]


def _infer_query(*, prompt: str, editor_context: EditorContext | None) -> str:
    terms = _extract_terms(prompt)
    if editor_context is not None:
        if editor_context.symbol_name:
            terms.extend(_extract_terms(editor_context.symbol_name))
        if editor_context.active_file:
            stem = Path(editor_context.active_file).stem
            terms.extend(_extract_terms(stem.replace("_", " ").replace("-", " ")))
        if editor_context.selection:
            terms.extend(_extract_terms(editor_context.selection)[:12])
    return " ".join(_dedupe(terms)[:24])


def _extract_terms(text: str) -> list[str]:
    return [term.lower() for term in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", text)]


def _normalize_to_repo_relative(root: Path, source: str) -> str | None:
    path = Path(source)
    try:
        resolved = path.resolve() if path.is_absolute() else (root / path).resolve()
        rel = resolved.relative_to(root)
        return str(rel)
    except Exception:
        try:
            rel = path.relative_to(root)
            return str(rel)
        except Exception:
            return None


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        normalized = item.strip().replace("\\", "/")
        if not normalized:
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        out.append(normalized)
    return out
