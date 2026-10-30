"""Constraint adherence and length ratio (EVAL-8, §6.1).

Per case section, each deterministic constraint is met or not, on the section's model text:

  max_words          words <= max_words * (1 + grading.word_tolerance)
  min_words          words >= min_words * (1 - grading.word_tolerance)
  paragraphs         prose paragraphs (not lists, tables, code or headings) == n
  list_items         list items == n
  forbidden_phrases  one check per phrase: absent (case-insensitive, whole words)

A section with no model text (missing, error, or a null-baseline section) fails every
length and count constraint; its forbidden-phrase checks pass.

Metrics:
  adherence     num = constraints met, den = constraints, pooled over the document
  length_ratio  value = mean over sections with max_words of words / max_words (tracked, not gated)
"""

from __future__ import annotations

import re
from typing import Any

from evals.core.types import CaseSection, EvalCase, GenerationResult, Score
from evals.graders.text import grading_config, is_prose_paragraph, list_items, paragraphs, words


def phrase_present(text: str, phrase: str) -> bool:
    return re.search(rf"(?i)(?<![A-Za-z0-9]){re.escape(phrase)}(?![A-Za-z0-9])", text) is not None


def check_section(section: CaseSection, text: str, tol: float) -> list[dict[str, Any]]:
    """One dict per constraint: {constraint, target, actual, met}."""
    c = section.constraints
    n_words = len(words(text))
    empty = not text.strip()
    out = []
    if c.max_words is not None:
        out.append({"constraint": "max_words", "target": c.max_words, "actual": n_words,
                    "met": not empty and n_words <= c.max_words * (1 + tol)})
    if c.min_words is not None:
        out.append({"constraint": "min_words", "target": c.min_words, "actual": n_words,
                    "met": not empty and n_words >= c.min_words * (1 - tol)})
    if c.paragraphs is not None:
        n = sum(1 for p in paragraphs(text) if is_prose_paragraph(p))
        out.append({"constraint": "paragraphs", "target": c.paragraphs, "actual": n, "met": not empty and n == c.paragraphs})
    if c.list_items is not None:
        n = list_items(text)
        out.append({"constraint": "list_items", "target": c.list_items, "actual": n, "met": not empty and n == c.list_items})
    for phrase in c.forbidden_phrases:
        present = phrase_present(text, phrase)
        out.append({"constraint": "forbidden_phrase", "target": phrase, "actual": present, "met": not present})
    return out


class AdherenceGrader:
    name = "adherence"
    version = "1"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.tol = float(grading_config(config)["word_tolerance"])

    def grade(self, case: EvalCase, result: GenerationResult) -> list[Score]:
        if result.error:
            return []
        by_id = {s.id: s for s in result.sections}
        met = total = 0
        ratios = []
        failed: dict[str, list[dict[str, Any]]] = {}
        for cs in case.sections:
            out = by_id.get(cs.id)
            text = out.model_text() if out else ""
            checks = check_section(cs, text, self.tol)
            met += sum(1 for c in checks if c["met"])
            total += len(checks)
            bad = [c for c in checks if not c["met"]]
            if bad:
                failed[cs.id] = bad
            if cs.constraints.max_words:
                ratios.append(len(words(text)) / cs.constraints.max_words)
        return [
            Score("adherence", met / total if total else None, met, total, {"failed": failed}),
            Score("length_ratio", sum(ratios) / len(ratios) if ratios else None, detail={"ratios": ratios}),
        ]
