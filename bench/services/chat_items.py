from __future__ import annotations

import re

from bench.schemas import ChatItem


CODE_FENCE_RE = re.compile(r"```([A-Za-z0-9_+\-#.]*)\n(.*?)```", re.DOTALL)


def split_response_items(text: str) -> list[ChatItem]:
    if not text.strip():
        return [ChatItem(kind="text", content="")]

    items: list[ChatItem] = []
    cursor = 0
    for match in CODE_FENCE_RE.finditer(text):
        start, end = match.span()
        if start > cursor:
            leading = text[cursor:start].strip()
            if leading:
                items.append(ChatItem(kind="text", content=leading))
        language = match.group(1).strip() or None
        code = match.group(2).strip()
        items.append(ChatItem(kind="code", content=code, language=language))
        cursor = end

    if cursor < len(text):
        trailing = text[cursor:].strip()
        if trailing:
            items.append(ChatItem(kind="text", content=trailing))

    if not items:
        items.append(ChatItem(kind="text", content=text.strip()))
    return items
