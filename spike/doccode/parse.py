"""FR-DYN-6 parsing helpers."""

from __future__ import annotations

import json as _json
import re

_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.*\S)\s*$")


def list_items(text: str) -> list[str]:
    items = []
    for line in text.splitlines():
        m = _ITEM.match(line)
        if m:
            items.append(re.sub(r"^(\*\*|__|\*|_)(.*)\1$", r"\2", m.group(1)).strip())
    if not items:
        raise ValueError(f"no Markdown list items found in: {text[:80]!r}")
    return items


def json(text: str):
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    body = m.group(1) if m else text
    for i, ch in enumerate(body):
        if ch in "[{":
            try:
                return _json.JSONDecoder().raw_decode(body[i:])[0]
            except ValueError:
                continue
    raise ValueError(f"no JSON found in: {text[:80]!r}")
