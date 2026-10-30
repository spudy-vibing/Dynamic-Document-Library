"""Scripted defects (EVALS_PROPOSAL §6.4). Each takes a clean result and its case and
returns a perturbed deep copy with the Markdown re-rendered; the input is never changed.

| perturbation      | defect                                                     | must move              |
|-------------------|------------------------------------------------------------|------------------------|
| copy_passage      | first two sentences of section 1 appended to the last one  | xrep, rep_pairs        |
| shift_figure      | one supported figure changed by +7%                        | unr                    |
| scale_figure_x10  | one supported figure multiplied by 10                      | unr (trap.t3 near key) |
| shuffle_sections  | section order reversed                                     | structure              |
| demote_heading    | second section rendered three levels deep (## -> ####)     | structure              |
| add_h1            | an H1 inserted into a section's model text                 | structure              |
| prepend_chatter   | "Sure! Here's the summary:" before section 1               | chatter                |
| insert_forbidden  | a forbidden phrase added to the first section that has one | adherence              |
| drop_data_block   | the first data block removed                               | data_exact             |
| alter_cell        | one digit of the first data block's first numeric cell     | data_exact             |
"""

from __future__ import annotations

import copy
import re
from typing import Callable

from evals.core.render import finalize
from evals.core.types import BlockOut, EvalCase, GenerationResult, SectionOut
from evals.graders.numbers import extract_numbers, match, supported_values
from evals.graders.text import grading_config, is_prose_paragraph, paragraphs

Perturbation = Callable[[GenerationResult, EvalCase], GenerationResult]


def _model_blocks(s: SectionOut) -> list[BlockOut]:
    return [b for b in s.blocks if b.origin == "model" and b.text.strip()]


def _model_sections(r: GenerationResult) -> list[SectionOut]:
    return [s for s in r.sections if _model_blocks(s)]


def _done(r: GenerationResult) -> GenerationResult:
    return finalize(r)


def copy_passage(result: GenerationResult, case: EvalCase) -> GenerationResult:
    r = copy.deepcopy(result)
    secs = _model_sections(r)
    if len(secs) < 2:
        raise ValueError("copy_passage needs two model-written sections")
    src = next(p for p in paragraphs(secs[0].model_text()) if is_prose_paragraph(p))
    passage = " ".join(re.split(r"(?<=[.!?])\s+", src.strip())[:2])
    dst = _model_blocks(secs[-1])[-1]
    dst.text = dst.text.rstrip() + "\n\n" + passage
    return _done(r)


def _reformat(raw_digits: str, factor: float) -> str:
    number = float(raw_digits.replace(",", ""))
    decimals = len(raw_digits.split(".")[1]) if "." in raw_digits else 0
    new = number * factor
    text = f"{new:,.{decimals}f}" if "," in raw_digits else f"{new:.{decimals}f}"
    if text == raw_digits:  # rounding swallowed the change: add one unit in the last place
        step = 10**-decimals
        text = f"{new + step:,.{decimals}f}" if "," in raw_digits else f"{new + step:.{decimals}f}"
    return text


def shift_figure(result: GenerationResult, case: EvalCase, factor: float = 1.07, key: str | None = None) -> GenerationResult:
    """Rewrite the first supported figure (or the first one matching ``key``) times ``factor``."""
    r = copy.deepcopy(result)
    values = supported_values(case)
    tol = float(grading_config()["number_tolerance"])
    for s in r.sections:
        for b in _model_blocks(s):
            for m in extract_numbers(b.text):
                k = match(m, values, tol)
                if k is None or (key is not None and k != key and not k.endswith("." + key)):
                    continue
                digits = b.text[m.num_start : m.num_end]
                b.text = b.text[: m.num_start] + _reformat(digits, factor) + b.text[m.num_end :]
                return _done(r)
    raise ValueError(f"no supported figure{' for ' + key if key else ''} to change in {result.case_id}")


def scale_figure_x10(result: GenerationResult, case: EvalCase, key: str | None = None) -> GenerationResult:
    return shift_figure(result, case, factor=10.0, key=key)


def shuffle_sections(result: GenerationResult, case: EvalCase) -> GenerationResult:
    r = copy.deepcopy(result)
    r.sections.reverse()
    return _done(r)


def demote_heading(result: GenerationResult, case: EvalCase) -> GenerationResult:
    r = copy.deepcopy(result)
    r.sections[min(1, len(r.sections) - 1)].level = 3
    return _done(r)


def add_h1(result: GenerationResult, case: EvalCase) -> GenerationResult:
    r = copy.deepcopy(result)
    b = _model_blocks(_model_sections(r)[0])[0]
    b.text = "# Overview\n\n" + b.text
    return _done(r)


def prepend_chatter(result: GenerationResult, case: EvalCase, text: str = "Sure! Here's the summary:") -> GenerationResult:
    r = copy.deepcopy(result)
    b = _model_blocks(_model_sections(r)[0])[0]
    b.text = f"{text}\n\n{b.text}"
    return _done(r)


def insert_forbidden(result: GenerationResult, case: EvalCase) -> GenerationResult:
    r = copy.deepcopy(result)
    by_id = {s.id: s for s in r.sections}
    for cs in case.sections:
        if cs.constraints.forbidden_phrases and cs.id in by_id and _model_blocks(by_id[cs.id]):
            b = _model_blocks(by_id[cs.id])[-1]
            b.text = b.text.rstrip() + f" This is a {cs.constraints.forbidden_phrases[0]} for the business."
            return _done(r)
    raise ValueError(f"{case.id}: no section with forbidden phrases and model text")


def _data_blocks(r: GenerationResult) -> list[BlockOut]:
    blocks = [b for s in r.sections for b in s.blocks if b.origin == "data"]
    if not blocks:
        raise ValueError(f"{r.case_id}: the result has no data blocks")
    return blocks


def drop_data_block(result: GenerationResult, case: EvalCase) -> GenerationResult:
    r = copy.deepcopy(result)
    target = _data_blocks(r)[0]
    for s in r.sections:
        s.blocks = [b for b in s.blocks if b is not target]
    return _done(r)


def _bump_digit(cell: str) -> str:
    i = next(i for i, ch in enumerate(cell) if ch.isdigit())
    return cell[:i] + str((int(cell[i]) + 1) % 10) + cell[i + 1 :]


def alter_cell(result: GenerationResult, case: EvalCase) -> GenerationResult:
    r = copy.deepcopy(result)
    b = _data_blocks(r)[0]
    if b.kind == "metric":
        old = b.value or ""
        b.value = _bump_digit(old)
        b.text = b.text.replace(f"| {old} |", f"| {b.value} |")
    else:
        for row in b.rows or []:
            for j, cell in enumerate(row):
                if any(ch.isdigit() for ch in cell):
                    old_line = "| " + " | ".join(row) + " |"
                    row[j] = _bump_digit(cell)
                    b.text = b.text.replace(old_line, "| " + " | ".join(row) + " |")
                    return _done(r)
    return _done(r)


# name -> (perturbation, metric it must move)
PERTURBATIONS: dict[str, tuple[Perturbation, str]] = {
    "copy_passage": (copy_passage, "xrep"),
    "shift_figure": (shift_figure, "unr"),
    "scale_figure_x10": (scale_figure_x10, "unr"),
    "shuffle_sections": (shuffle_sections, "structure"),
    "demote_heading": (demote_heading, "structure"),
    "add_h1": (add_h1, "structure"),
    "prepend_chatter": (prepend_chatter, "chatter"),
    "insert_forbidden": (insert_forbidden, "adherence"),
    "drop_data_block": (drop_data_block, "data_exact"),
    "alter_cell": (alter_cell, "data_exact"),
}
