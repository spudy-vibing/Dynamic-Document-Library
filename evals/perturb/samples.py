"""Build a ``GenerationResult`` from hand-written section text plus harness data blocks.

A sample file is JSON: ``{"case_id": str, "sections": {section_id: markdown, ...}}``.
Each section becomes one ``model`` block followed by the data blocks the harness builds
from the case (``core.render.data_blocks_for``), in outline order, then the Markdown is
rendered with ``core.render.finalize`` exactly as the baselines do.
"""

from __future__ import annotations

import json
from pathlib import Path

from evals.core.render import data_blocks_for, finalize
from evals.core.types import BlockOut, EvalCase, GenerationResult, SectionOut


def build_result(case: EvalCase, texts: dict[str, str], *, generator: str = "sample") -> GenerationResult:
    sections = []
    for s in case.sections:
        blocks = [BlockOut(origin="model", text=texts[s.id].strip())] if texts.get(s.id, "").strip() else []
        blocks += data_blocks_for(case, s.id)
        sections.append(SectionOut(id=s.id, title=s.title, level=1, blocks=blocks))
    result = GenerationResult(
        generator=generator,
        generator_version="1",
        case_id=case.id,
        rep=1,
        title=case.title,
        sections=sections,
        calls=[],
        wall_ms=0,
    )
    return finalize(result)


def load_sample(path: Path, case: EvalCase) -> GenerationResult:
    raw = json.loads(Path(path).read_text())
    if raw["case_id"] != case.id:
        raise ValueError(f"{path}: sample is for {raw['case_id']}, not {case.id}")
    return build_result(case, raw["sections"])
