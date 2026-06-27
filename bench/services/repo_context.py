from __future__ import annotations

import re
from pathlib import Path

from bench.schemas import RepoContextConfig


def build_repo_context(config: RepoContextConfig) -> tuple[str, dict[str, object]]:
    allowed_exts = {ext.lower() for ext in config.include_extensions}
    root = Path(config.root_path).resolve()
    if not root.exists():
        return "", {"root_path": str(root), "files_included": 0, "error": "root_path_missing"}

    query_terms = _normalize_terms(config.query)
    focused_paths = {(root / path).resolve() for path in config.focus_paths}
    candidates: list[tuple[int, Path, str]] = []

    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in allowed_exts:
            continue
        if any(part.startswith(".") for part in path.relative_to(root).parts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        except OSError:
            continue

        score = _score_file(path, text, query_terms, focused_paths)
        if score <= 0 and query_terms:
            continue
        candidates.append((score, path, text))

    candidates.sort(key=lambda item: (-item[0], str(item[1])))

    included: list[str] = []
    total_chars = 0
    files_included = 0
    if config.include_file_tree:
        tree = _build_file_tree(root, [path for _, path, _ in candidates[: config.max_files]])
        if tree:
            tree_block = f"FILE TREE:\n{tree}"
            included.append(tree_block)
            total_chars += len(tree_block) + 2

    for score, path, text in candidates:
        snippet_body = _make_snippet(
            text=text,
            query_terms=query_terms,
            max_chars=config.max_file_chars,
            context_lines=config.snippet_context_lines,
        )
        snippet = (
            f"FILE: {path.relative_to(root)}\n"
            f"SCORE: {score}\n"
            f"{snippet_body}"
        ).strip()
        projected = total_chars + len(snippet) + 2
        if projected > config.max_total_chars or files_included >= config.max_files:
            break

        included.append(snippet)
        total_chars = projected
        files_included += 1

    summary = {
        "root_path": str(root),
        "files_included": files_included,
        "max_files": config.max_files,
        "max_total_chars": config.max_total_chars,
        "max_file_chars": config.max_file_chars,
        "query_terms": query_terms,
        "focus_paths": config.focus_paths,
        "strategy": "ranked_snippets",
    }
    return "\n\n".join(included), summary


def _normalize_terms(query: str) -> list[str]:
    return [term for term in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", query.lower())[:24]]


def _score_file(
    path: Path,
    text: str,
    query_terms: list[str],
    focused_paths: set[Path],
) -> int:
    score = 1
    lowered_text = text.lower()
    lowered_name = path.name.lower()
    if any(path == focused or focused in path.parents for focused in focused_paths):
        score += 100
    for term in query_terms:
        if term in lowered_name:
            score += 25
        occurrences = lowered_text.count(term)
        if occurrences:
            score += min(occurrences, 10) * 5
    return score


def _build_file_tree(root: Path, paths: list[Path]) -> str:
    rels = [str(path.relative_to(root)) for path in paths[:40]]
    return "\n".join(rels)


def _make_snippet(
    *,
    text: str,
    query_terms: list[str],
    max_chars: int,
    context_lines: int,
) -> str:
    if not query_terms:
        return text[:max_chars].strip()

    lines = text.splitlines()
    hit_indexes: list[int] = []
    for idx, line in enumerate(lines):
        lowered = line.lower()
        if any(term in lowered for term in query_terms):
            hit_indexes.append(idx)
    if not hit_indexes:
        return text[:max_chars].strip()

    chunks: list[str] = []
    for idx in hit_indexes[:6]:
        start = max(0, idx - context_lines)
        end = min(len(lines), idx + context_lines + 1)
        block = "\n".join(lines[start:end]).strip()
        if block:
            chunks.append(block)
    joined = "\n...\n".join(chunks)
    return joined[:max_chars].strip()
