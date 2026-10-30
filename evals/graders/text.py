"""Small Markdown text helpers shared by the graders (no third-party parsing)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from evals.core.config import load_config
from evals.core.types import EvalCase, GenerationResult, SectionOut

_FENCE = re.compile(r"^\s*(```|~~~)")
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_LIST_ITEM = re.compile(r"^\s{0,3}(?:[-*+]|\d{1,3}[.)])\s+\S")
_WORD = re.compile(r"[A-Za-z0-9]+(?:['’.,-][A-Za-z0-9]+)*")


def grading_config(config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = config if config is not None else load_config()
    g = dict(cfg.get("grading", {}))
    g.setdefault("number_tolerance", 0.005)
    g.setdefault("ngram", 6)
    g.setdefault("word_tolerance", 0.10)
    return g


@dataclass
class Heading:
    level: int
    text: str
    line: int


def headings(markdown: str) -> list[Heading]:
    """ATX headings outside fenced code blocks."""
    out, in_fence = [], False
    for i, line in enumerate(markdown.splitlines()):
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = _HEADING.match(line)
        if m:
            out.append(Heading(len(m.group(1)), m.group(2).strip(), i))
    return out


def norm_title(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def paragraphs(text: str) -> list[str]:
    """Blank-line separated chunks (a whole list or code block is one chunk)."""
    chunks, cur, in_fence = [], [], False
    for line in text.splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
        if not in_fence and not line.strip():
            if cur:
                chunks.append("\n".join(cur))
                cur = []
            continue
        cur.append(line)
    if cur:
        chunks.append("\n".join(cur))
    return [c for c in chunks if c.strip()]


def is_prose_paragraph(chunk: str) -> bool:
    """A paragraph of prose: not a heading, list, table, code block or rule."""
    first = chunk.lstrip().splitlines()[0]
    return not (
        _HEADING.match(first)
        or _LIST_ITEM.match(first)
        or first.startswith("|")
        or _FENCE.match(first)
        or re.fullmatch(r"\s*([-*_])\s*(\1\s*){2,}", first)
    )


def list_items(text: str) -> int:
    count, in_fence = 0, False
    for line in text.splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if not in_fence and _LIST_ITEM.match(line):
            count += 1
    return count


def words(text: str) -> list[str]:
    """Words of the text with Markdown markers and heading hashes ignored."""
    return _WORD.findall(text)


def model_sections(result: GenerationResult) -> list[SectionOut]:
    """Sections that contain at least some model-written text, in result order."""
    return [s for s in result.sections if s.model_text().strip()]


def case_section_ids(case: EvalCase) -> list[str]:
    return [s.id for s in case.sections]
