from __future__ import annotations

import ast
import hashlib
import json
import re
from pathlib import Path

from bench.schemas import RepoContextConfig


def build_repo_context(config: RepoContextConfig) -> tuple[str, dict[str, object]]:
    allowed_exts = {ext.lower() for ext in config.include_extensions}
    root = Path(config.root_path).resolve()
    if not root.exists():
        return "", {"root_path": str(root), "files_included": 0, "error": "root_path_missing"}

    file_manifest = _collect_file_manifest(root, allowed_exts)
    cache_key = _build_cache_key(root, config, file_manifest)
    cache = _RepoContextCache()
    cached = cache.get(cache_key)
    if cached is not None:
        cached_summary = dict(cached["summary"])
        cached_summary["cache_hit"] = True
        return cached["context"], cached_summary

    query_terms = _normalize_terms(config.query)
    focused_paths = {(root / path).resolve() for path in config.focus_paths}
    candidates: list[tuple[int, Path, str]] = []

    for path in file_manifest:
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
        summary_body = _compress_file(
            path=path,
            text=text,
            query_terms=query_terms,
            max_chars=config.max_file_chars,
            context_lines=config.snippet_context_lines,
        )
        snippet = (
            f"FILE: {path.relative_to(root)}\n"
            f"SCORE: {score}\n"
            f"{summary_body}"
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
        "strategy": "compressed_repo_map",
        "cache_hit": False,
    }
    context = "\n\n".join(included)
    cache.set(cache_key, {"context": context, "summary": summary})
    return context, summary


def _collect_file_manifest(root: Path, allowed_exts: set[str]) -> list[Path]:
    files: list[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in allowed_exts:
            continue
        if any(part.startswith(".") for part in path.relative_to(root).parts):
            continue
        files.append(path)
    return files


def _build_cache_key(
    root: Path,
    config: RepoContextConfig,
    file_manifest: list[Path],
) -> str:
    payload = {
        "root": str(root),
        "config": {
            "include_extensions": config.include_extensions,
            "focus_paths": config.focus_paths,
            "query": config.query,
            "include_file_tree": config.include_file_tree,
            "max_files": config.max_files,
            "max_file_chars": config.max_file_chars,
            "max_total_chars": config.max_total_chars,
            "snippet_context_lines": config.snippet_context_lines,
        },
        "files": [
            {
                "path": str(path.relative_to(root)),
                "mtime_ns": path.stat().st_mtime_ns,
                "size": path.stat().st_size,
            }
            for path in file_manifest
        ],
    }
    encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


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


def _compress_file(
    *,
    path: Path,
    text: str,
    query_terms: list[str],
    max_chars: int,
    context_lines: int,
) -> str:
    parts = [f"TYPE: {_file_kind(path)}"]

    module_summary = _summarize_file_structure(path, text)
    if module_summary:
        parts.append(module_summary)

    targeted_excerpt = _make_targeted_excerpt(
        text=text,
        query_terms=query_terms,
        max_chars=max(300, max_chars // 3),
        context_lines=context_lines,
    )
    if targeted_excerpt:
        parts.append(f"RELEVANT EXCERPTS:\n{targeted_excerpt}")

    compressed = "\n".join(part for part in parts if part.strip()).strip()
    if not compressed:
        compressed = text[:max_chars].strip()
    return compressed[:max_chars].strip()


def _file_kind(path: Path) -> str:
    return {
        ".py": "python",
        ".ts": "typescript",
        ".tsx": "tsx",
        ".js": "javascript",
        ".jsx": "jsx",
        ".md": "markdown",
        ".json": "json",
        ".toml": "toml",
        ".yml": "yaml",
        ".yaml": "yaml",
    }.get(path.suffix.lower(), path.suffix.lower().lstrip(".") or "text")


def _summarize_file_structure(path: Path, text: str) -> str:
    if path.suffix.lower() == ".py":
        return _summarize_python(text)
    return _summarize_generic(text)


def _summarize_python(text: str) -> str:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return _summarize_generic(text)

    lines: list[str] = []
    doc = ast.get_docstring(tree)
    if doc:
        lines.append(f"MODULE DOC: {doc.splitlines()[0][:160]}")

    imports: list[str] = []
    functions: list[str] = []
    classes: list[str] = []

    for node in tree.body:
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names[:4])
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            names = ", ".join(alias.name for alias in node.names[:4])
            imports.append(f"{module}: {names}".strip())
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append(_format_python_function(node))
        elif isinstance(node, ast.ClassDef):
            classes.append(_format_python_class(node))

    if imports:
        lines.append("IMPORTS: " + "; ".join(imports[:8]))
    if classes:
        lines.append("CLASSES:")
        lines.extend(f"- {entry}" for entry in classes[:8])
    if functions:
        lines.append("FUNCTIONS:")
        lines.extend(f"- {entry}" for entry in functions[:12])
    return "\n".join(lines)


def _format_python_function(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    args = [arg.arg for arg in node.args.args[:6]]
    signature = f"{node.name}({', '.join(args)})"
    doc = ast.get_docstring(node)
    if doc:
        return f"{signature} - {doc.splitlines()[0][:120]}"
    return signature


def _format_python_class(node: ast.ClassDef) -> str:
    methods: list[str] = []
    for child in node.body:
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            methods.append(child.name)
    preview = ", ".join(methods[:6])
    suffix = f" methods: {preview}" if preview else ""
    doc = ast.get_docstring(node)
    if doc:
        suffix += f" - {doc.splitlines()[0][:100]}"
    return f"{node.name}{suffix}"


def _summarize_generic(text: str) -> str:
    lines: list[str] = []

    import_hits = re.findall(
        r"^(?:import|from|export\s+.*?from)\s+.+$",
        text,
        flags=re.MULTILINE,
    )
    symbol_hits = re.findall(
        r"^(?:export\s+)?(?:async\s+)?(?:function|class|const|let|var|interface|type)\s+([A-Za-z_][A-Za-z0-9_]*)",
        text,
        flags=re.MULTILINE,
    )
    heading_hits = re.findall(r"^(#+\s+.+)$", text, flags=re.MULTILINE)
    comment_hits = re.findall(r"^(?:#|//)\s+(.+)$", text, flags=re.MULTILINE)

    if heading_hits:
        lines.append("HEADINGS:")
        lines.extend(f"- {entry[:120]}" for entry in heading_hits[:6])
    if comment_hits:
        lines.append("COMMENTS:")
        lines.extend(f"- {entry[:120]}" for entry in comment_hits[:6])
    if import_hits:
        lines.append("IMPORTS:")
        lines.extend(f"- {entry[:120]}" for entry in import_hits[:8])
    if symbol_hits:
        lines.append("SYMBOLS: " + ", ".join(symbol_hits[:16]))

    return "\n".join(lines) or text[:400].strip()


def _make_targeted_excerpt(
    *,
    text: str,
    query_terms: list[str],
    max_chars: int,
    context_lines: int,
) -> str:
    if not query_terms:
        return ""

    lines = text.splitlines()
    hit_indexes: list[int] = []
    for idx, line in enumerate(lines):
        lowered = line.lower()
        if any(term in lowered for term in query_terms):
            hit_indexes.append(idx)
    if not hit_indexes:
        return ""

    chunks: list[str] = []
    for idx in hit_indexes[:4]:
        start = max(0, idx - context_lines)
        end = min(len(lines), idx + context_lines + 1)
        block = "\n".join(lines[start:end]).strip()
        if block:
            chunks.append(block)
    joined = "\n...\n".join(chunks)
    return joined[:max_chars].strip()


class _RepoContextCache:
    def __init__(self, path: str = ".bench_repo_context_cache.json"):
        self.path = Path(path)
        self._data = self._load()

    def _load(self) -> dict[str, dict[str, object]]:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return raw if isinstance(raw, dict) else {}

    def get(self, key: str) -> dict[str, object] | None:
        value = self._data.get(key)
        return value if isinstance(value, dict) else None

    def set(self, key: str, value: dict[str, object]) -> None:
        self._data[key] = value
        try:
            self.path.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
        except OSError:
            pass
