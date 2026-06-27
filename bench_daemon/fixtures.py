from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .paths import FIXTURES_ROOT


_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


class FixtureError(ValueError):
    """Raised when a fixture or candidate request is invalid."""


@dataclass(frozen=True)
class FixtureConfig:
    id: str
    label: str
    language: str
    target_file: str
    runner: str
    dockerfile: str
    docker_context: str
    docker_image: str
    timeout_ms: int
    candidates_dir: str
    root: Path

    @property
    def target_path(self) -> Path:
        return self.root / self.target_file

    @property
    def dockerfile_path(self) -> Path:
        return self.root / self.dockerfile

    @property
    def docker_context_path(self) -> Path:
        return self.root / self.docker_context

    @property
    def candidates_path(self) -> Path:
        return self.root / self.candidates_dir

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "language": self.language,
            "target_file": self.target_file,
            "runner": self.runner,
            "docker_image": self.docker_image,
            "timeout_ms": self.timeout_ms,
            "candidates_dir": self.candidates_dir,
        }


@dataclass(frozen=True)
class CandidateInput:
    candidate_id: str
    label: str
    rationale: str | None
    files: dict[str, str]


def list_fixtures(fixtures_root: Path = FIXTURES_ROOT) -> list[FixtureConfig]:
    fixtures: list[FixtureConfig] = []
    if not fixtures_root.exists():
        return fixtures

    for bench_file in sorted(fixtures_root.glob("*/bench.json")):
        fixtures.append(load_fixture_from_path(bench_file))
    return fixtures


def load_fixture(fixture_id: str, fixtures_root: Path = FIXTURES_ROOT) -> FixtureConfig:
    for fixture in list_fixtures(fixtures_root):
        if fixture.id == fixture_id:
            return fixture
    raise FixtureError(f"Unknown fixture_id: {fixture_id}")


def load_fixture_from_path(bench_file: Path) -> FixtureConfig:
    root = bench_file.parent.resolve()
    try:
        raw = json.loads(bench_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise FixtureError(f"Invalid JSON in {bench_file}: {exc}") from exc

    fixture = FixtureConfig(
        id=_required_string(raw, "id"),
        label=_required_string(raw, "label"),
        language=_required_string(raw, "language"),
        target_file=_required_string(raw, "target_file"),
        runner=_required_string(raw, "runner"),
        dockerfile=_required_string(raw, "dockerfile"),
        docker_context=_required_string(raw, "docker_context"),
        docker_image=_required_string(raw, "docker_image"),
        timeout_ms=_required_int(raw, "timeout_ms"),
        candidates_dir=_required_string(raw, "candidates_dir"),
        root=root,
    )

    _validate_id(fixture.id, "fixture id")
    _validate_relative_file(root, fixture.target_file, "target_file")
    _validate_relative_file(root, fixture.dockerfile, "dockerfile")
    _validate_relative_dir(root, fixture.docker_context, "docker_context")
    _validate_relative_dir(root, fixture.candidates_dir, "candidates_dir")
    if fixture.timeout_ms <= 0:
        raise FixtureError("timeout_ms must be greater than zero")

    return fixture


def load_candidate_files(fixture: FixtureConfig) -> list[CandidateInput]:
    candidates: list[CandidateInput] = []
    for candidate_file in sorted(fixture.candidates_path.glob("*.py")):
        candidate_id = candidate_file.stem
        _validate_id(candidate_id, "candidate_id")
        code = candidate_file.read_text(encoding="utf-8")
        candidates.append(
            CandidateInput(
                candidate_id=candidate_id,
                label=_label_from_id(candidate_id),
                rationale=None,
                files={fixture.target_file: code},
            )
        )

    if not candidates:
        raise FixtureError(f"No candidates found in {fixture.candidates_path}")
    return candidates


def parse_candidate_request(
    fixture: FixtureConfig, candidates: list[dict[str, Any]] | None
) -> list[CandidateInput]:
    if candidates is None:
        return load_candidate_files(fixture)

    parsed: list[CandidateInput] = []
    seen: set[str] = set()
    for index, raw in enumerate(candidates):
        if not isinstance(raw, dict):
            raise FixtureError(f"Candidate at index {index} must be an object")

        candidate_id = _required_string(raw, "candidate_id")
        _validate_id(candidate_id, "candidate_id")
        if candidate_id in seen:
            raise FixtureError(f"Duplicate candidate_id: {candidate_id}")
        seen.add(candidate_id)

        files = raw.get("files")
        if not isinstance(files, dict):
            raise FixtureError(f"Candidate {candidate_id} must include files")
        if fixture.target_file not in files:
            raise FixtureError(
                f"Candidate {candidate_id} must include {fixture.target_file}"
            )

        normalized_files: dict[str, str] = {}
        for relative_path, contents in files.items():
            if not isinstance(relative_path, str) or not relative_path:
                raise FixtureError(f"Candidate {candidate_id} has invalid file path")
            if not isinstance(contents, str):
                raise FixtureError(
                    f"Candidate {candidate_id} file {relative_path} must be text"
                )
            _validate_relative_path(relative_path, f"candidate file {relative_path}")
            normalized_files[relative_path] = contents

        label = _optional_string(raw, "label", candidate_id)
        rationale = _optional_string(raw, "rationale", candidate_id)
        parsed.append(
            CandidateInput(
                candidate_id=candidate_id,
                label=label or _label_from_id(candidate_id),
                rationale=rationale,
                files=normalized_files,
            )
        )

    if not parsed:
        raise FixtureError("At least one candidate is required")
    return parsed


def _required_string(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise FixtureError(f"bench.json must include non-empty string {key}")
    return value


def _required_int(raw: dict[str, Any], key: str) -> int:
    value = raw.get(key)
    if not isinstance(value, int):
        raise FixtureError(f"bench.json must include integer {key}")
    return value


def _optional_string(
    raw: dict[str, Any], key: str, candidate_id: str
) -> str | None:
    if key not in raw or raw[key] is None:
        return None
    value = raw[key]
    if not isinstance(value, str):
        raise FixtureError(f"Candidate {candidate_id} {key} must be text")
    if key == "label" and not value:
        raise FixtureError(f"Candidate {candidate_id} label must not be empty")
    return value or None


def _validate_id(value: str, label: str) -> None:
    if not _ID_PATTERN.match(value):
        raise FixtureError(
            f"Invalid {label}: {value}. Use letters, numbers, dot, dash, or underscore."
        )


def _validate_relative_file(root: Path, relative_path: str, field: str) -> None:
    resolved = _validate_relative_path(relative_path, field, root)
    if not resolved.is_file():
        raise FixtureError(f"{field} does not exist or is not a file: {relative_path}")


def _validate_relative_dir(root: Path, relative_path: str, field: str) -> None:
    resolved = _validate_relative_path(relative_path, field, root)
    if not resolved.is_dir():
        raise FixtureError(f"{field} does not exist or is not a directory: {relative_path}")


def _validate_relative_path(
    relative_path: str, field: str, root: Path | None = None
) -> Path:
    path = Path(relative_path)
    if path.is_absolute() or ".." in path.parts:
        raise FixtureError(f"{field} must be a relative path inside the fixture")

    if root is None:
        return path

    resolved_root = root.resolve()
    resolved = (resolved_root / path).resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise FixtureError(f"{field} must stay inside the fixture") from exc
    return resolved


def _label_from_id(candidate_id: str) -> str:
    return candidate_id.replace("_", " ").replace("-", " ").title()
