"""Helpers shared by the baselines: prompt files, the cached context block, section assembly.

Baselines measure raw model output: nothing here cleans model text. The harness only
inserts data blocks (origin "data", via core.render) at the start of each section and
renders the final Markdown with core.render.finalize.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from evals.core.render import data_blocks_for, finalize
from evals.core.types import LLM, BlockOut, CaseSection, Constraints, EvalCase, GenerationResult, SectionOut

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
_PART = re.compile(r"^=== (\w+) ===\s*$", re.M)
_VERSION = re.compile(r"version:\s*([\w.-]+)")
_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")


# ---------------------------------------------------------------- prompt files


@dataclass(frozen=True)
class PromptFile:
    """A frozen, versioned prompt file: ``<!-- ... version: N -->`` then ``=== part ===`` blocks."""

    name: str
    version: str
    sha: str
    parts: dict[str, str]

    def render(self, part: str, **values: Any) -> str:
        template = self.parts[part]

        def sub(m: re.Match[str]) -> str:
            key = m.group(1)
            if key not in values:
                raise KeyError(f"{self.name}:{part} needs {{{{{key}}}}}")
            return str(values[key])

        return _PLACEHOLDER.sub(sub, template)

    @property
    def tag(self) -> str:
        return f"{self.name}@{self.version}-{self.sha[:8]}"


@lru_cache(maxsize=None)
def load_prompt(name: str, root: Path = PROMPTS_DIR) -> PromptFile:
    raw = (root / f"{name}.md").read_text(encoding="utf-8")
    header, _, _ = raw.partition("===")
    m = _VERSION.search(header)
    if not m:
        raise ValueError(f"prompt {name}.md has no version line")
    pieces = _PART.split(raw)
    parts = {pieces[i]: pieces[i + 1].strip("\n") for i in range(1, len(pieces) - 1, 2)}
    return PromptFile(name=name, version=m.group(1), sha=hashlib.sha256(raw.encode()).hexdigest(), parts=parts)


def generator_version(logic: str, *prompts: PromptFile) -> str:
    """``<logic>+<prompt>@<version>-<sha8>...``: any prompt edit changes the version."""
    return "+".join([logic, *(p.tag for p in prompts)])


SHARED = "_shared"


def shared() -> PromptFile:
    return load_prompt(SHARED)


# ---------------------------------------------------------------- prompt pieces


def data_json(case: EvalCase) -> str:
    # Key order is the fixture's; indent fixed. Byte-stable, so provider-side caching applies.
    return json.dumps(case.data, indent=2, ensure_ascii=False)


def context_text(case: EvalCase) -> str:
    sh = shared()
    attachments = "\n".join(
        sh.render("attachment", name=name, text=case.attachments[name].rstrip("\n")) for name in sorted(case.attachments)
    )
    return sh.render("context", data_json=data_json(case), attachments=attachments).rstrip() + "\n"


def context_block(case: EvalCase) -> dict[str, Any]:
    """The large shared prefix (data + attachments), first in every request, with a cache breakpoint.

    It is byte-identical for every call of a document, so B1's parallel calls and B2's
    turns can read it from the provider's prompt cache (below the model's minimum
    cacheable size it silently does not cache).
    """
    return {"type": "text", "text": context_text(case), "cache_control": {"type": "ephemeral"}}


def user_message(case: EvalCase, text: str) -> dict[str, Any]:
    return {"role": "user", "content": [context_block(case), {"type": "text", "text": text}]}


def document_text(case: EvalCase) -> str:
    return shared().render(
        "document", title=case.title, purpose=case.purpose, audience=case.audience or "-", style=case.style or "-"
    )


def constraints_text(c: Constraints) -> str:
    items = []
    if c.min_words and c.max_words:
        items.append(f"{c.min_words}-{c.max_words} words")
    elif c.max_words:
        items.append(f"at most {c.max_words} words")
    elif c.min_words:
        items.append(f"at least {c.min_words} words")
    if c.paragraphs:
        items.append(f"exactly {c.paragraphs} paragraph{'s' if c.paragraphs != 1 else ''}")
    if c.list_items:
        items.append(f"exactly {c.list_items} list items")
    if c.forbidden_phrases:
        items.append("do not use the phrases " + ", ".join(f'"{p}"' for p in c.forbidden_phrases))
    return "\nConstraints: " + "; ".join(items) + "." if items else ""


def data_blocks_text(section: CaseSection) -> str:
    if not section.data_blocks:
        return ""
    names = []
    for b in section.data_blocks:
        label = b.label or b.key
        names.append(f'{b.kind} "{label}"' + (f" ({b.caption})" if b.caption else ""))
    return "\nInserted automatically before your text (do not reproduce): " + "; ".join(names) + "."


def section_entry(section: CaseSection) -> str:
    return shared().render(
        "section_entry",
        id=section.id,
        title=section.title,
        brief=section.brief,
        instruction=section.instruction,
        constraints=constraints_text(section.constraints),
        data_blocks=data_blocks_text(section),
    )


def outline_text(case: EvalCase) -> str:
    return "\n".join(section_entry(s) for s in case.sections)


def fact_rules() -> str:
    return shared().render("fact_rules")


def system_prompt() -> str:
    return shared().render("system")


# ---------------------------------------------------------------- assembling results


def section_out(case: EvalCase, section: CaseSection, text: str, warnings: list[str]) -> SectionOut:
    """Data blocks first (origin "data"), then the model's text unchanged (origin "model").

    A section with no model text gets an empty origin-"error" block, which graders count
    as a missing section; a warning says why.
    """
    blocks = data_blocks_for(case, section.id)
    if text.strip():
        blocks.append(BlockOut(origin="model", text=text))
    else:
        blocks.append(BlockOut(origin="error", text=""))
        warnings.append(f"section {section.id}: no model text")
    return SectionOut(id=section.id, title=section.title, level=1, blocks=blocks)


def assemble(
    generator: Any, case: EvalCase, rep: int, sections: list[SectionOut], llm: LLM, t0: float, warnings: list[str]
) -> GenerationResult:
    result = GenerationResult(
        generator=generator.name,
        generator_version=generator.version,
        case_id=case.id,
        rep=rep,
        title=case.title,
        sections=sections,
        calls=list(llm.calls),
        wall_ms=int((time.perf_counter() - t0) * 1000),
        warnings=warnings,
    )
    return finalize(result)


# ---------------------------------------------------------------- splitting a one-shot document

_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")
_FENCE = re.compile(r"^\s*(```|~~~)")


def norm_title(title: str) -> str:
    t = re.sub(r"[*_`]", "", title).strip().lower()
    t = re.sub(r"^(section\s+)?\d+[.):]?\s+", "", t)  # "1. Summary", "Section 2: ..."
    t = re.sub(r"[\s:.\-–—]+$", "", t)
    return re.sub(r"\s+", " ", t)


def split_document(case: EvalCase, text: str) -> tuple[list[SectionOut], list[str]]:
    """Split a one-shot document into sections by headings that match outline titles.

    Rules (explicit, so the structure grader scores what the model actually did):
    - A heading of level 1-3 whose normalised text equals an outline title starts that
      section (first occurrence only). Any other heading stays inside the current
      section's text, unchanged.
    - Text before the first matched heading (preamble, a document title) is kept and
      prepended to the first section found; with no matched heading at all, the whole
      output becomes the first outline section's text.
    - Sections keep the order the model wrote them in. Outline sections the model did
      not write are appended at the end with an empty origin-"error" block.
    """
    by_title = {norm_title(s.title): s for s in case.sections}
    warnings: list[str] = []
    chunks: list[tuple[CaseSection, list[str]]] = []
    preamble: list[str] = []
    seen: set[str] = set()
    in_fence = False
    for line in text.splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
        m = None if in_fence else _HEADING.match(line)
        sec = by_title.get(norm_title(m.group(2))) if m else None
        if m and sec is not None and sec.id not in seen and len(m.group(1)) <= 3:
            seen.add(sec.id)
            if len(m.group(1)) != 2:
                warnings.append(f"section {sec.id}: heading level {len(m.group(1))}")
            chunks.append((sec, []))
            continue
        (chunks[-1][1] if chunks else preamble).append(line)

    pre = "\n".join(preamble).strip()
    if not chunks:
        chunks = [(case.sections[0], preamble)]
        seen.add(case.sections[0].id)
        warnings.append("no outline headings found; whole output assigned to the first section")
    elif pre:
        chunks[0] = (chunks[0][0], preamble + [""] + chunks[0][1])
        warnings.append(f"text before the first heading kept in section {chunks[0][0].id}")

    expected = [s.id for s in case.sections]
    written = [sec.id for sec, _ in chunks]
    if written != [i for i in expected if i in written]:
        warnings.append("sections out of outline order")

    sections = [section_out(case, sec, "\n".join(lines).strip("\n"), warnings) for sec, lines in chunks]
    for s in case.sections:
        if s.id not in seen:
            warnings.append(f"section {s.id}: missing from model output")
            sections.append(section_out(case, s, "", []))
    return sections, warnings


def chars_to_tokens(chars: int, chars_per_token: float) -> int:
    return int(chars / chars_per_token) + 1
