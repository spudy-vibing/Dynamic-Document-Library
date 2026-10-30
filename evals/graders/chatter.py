"""Chatter rate (EVAL-8, §6.1).

A section chatters if the first or last paragraph of its model text matches the
lexicon below, which is deliberately broader than FR-OUT-3's trimmer: openers such as
"Sure", "Certainly", "Of course", "Here is/Here's", "Below is"; sign-offs such as
"I hope", "Let me know", "Feel free"; "As an AI"; "In this section, we will";
addressing the user ("you asked", "as requested"); and an "In conclusion" outside the
last section.

Metric: chatter, num = sections that chatter, den = sections with model text.
"""

from __future__ import annotations

import re

from evals.core.types import EvalCase, GenerationResult, Score
from evals.graders.text import model_sections, paragraphs

_OPENERS = re.compile(
    r"(?i)^\W*(?:sure|certainly|of course|absolutely|okay|great question|happy to help"
    r"|here(?:'s|’s| is| are)|below (?:is|are|you(?:'ll|’ll| will) find)"
    r"|in this section,? (?:we|i) (?:will|'ll|’ll)|this section (?:will|aims to))\b"
)
_ANYWHERE = re.compile(
    r"(?i)\b(?:i hope|hope this helps|let me know|feel free|as an ai|as a language model|i(?:'d|’d| would) be happy"
    r"|you asked|as (?:you )?requested|in this section,? (?:we|i) (?:will|'ll|’ll)|i(?:'ve|’ve| have) (?:written|drafted|prepared))\b"
)
_CONCLUSION = re.compile(r"(?i)^\W*in conclusion\b")


class ChatterGrader:
    name = "chatter"
    version = "1"

    def grade(self, case: EvalCase, result: GenerationResult) -> list[Score]:
        if result.error:
            return []
        sections = model_sections(result)
        last_id = result.sections[-1].id if result.sections else None
        hits = []
        for s in sections:
            paras = paragraphs(s.model_text())
            edges = [paras[0]] + ([paras[-1]] if len(paras) > 1 else [])
            matched = None
            for p in edges:
                m = _OPENERS.search(p) or _ANYWHERE.search(p)
                if not m and s.id != last_id:
                    m = _CONCLUSION.search(p)
                if m:
                    matched = m.group(0).strip()
                    break
            if matched:
                hits.append({"section": s.id, "match": matched})
        n = len(sections)
        return [Score("chatter", len(hits) / n if n else None, len(hits), n, {"hits": hits})]
