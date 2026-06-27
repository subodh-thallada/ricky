from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class TextGenerationResult:
    model: str
    text: str | None
    usage: dict[str, Any] | None = None
    raw: Any | None = None
