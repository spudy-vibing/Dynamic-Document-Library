"""Neutral rendering shared by every generator.

Data blocks are built from the case, never from model output, and rendered the same
way for every generator, so neither graders nor the judge can tell generators apart
by how they format data (EVALS_PROPOSAL §4.1).
"""

from __future__ import annotations

from typing import Any

from evals.core.cases import resolve_key
from evals.core.types import BlockOut, DataBlockSpec, EvalCase, GenerationResult, SectionOut


def fmt_value(v: Any) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    if isinstance(v, int):
        return f"{v:,}"
    return str(v)


def build_data_block(case: EvalCase, spec: DataBlockSpec) -> BlockOut:
    value = resolve_key(case.data, spec.key)
    if spec.kind == "metric":
        label = spec.label or spec.key
        shown = fmt_value(value)
        return BlockOut(
            origin="data",
            kind="metric",
            label=label,
            value=shown,
            data_key=spec.key,
            text=f"| Metric | Value |\n| --- | --- |\n| {label} | {shown} |",
        )
    if not isinstance(value, list) or not value or not all(isinstance(r, dict) for r in value):
        raise ValueError(f"{case.id}: table key {spec.key} must point at a non-empty list of dicts")
    columns = list(value[0].keys())
    rows = [[fmt_value(r.get(c)) for c in columns] for r in value]
    lines = ["| " + " | ".join(columns) + " |", "| " + " | ".join("---" for _ in columns) + " |"]
    lines += ["| " + " | ".join(r) + " |" for r in rows]
    if spec.caption:
        lines.append(f"\n*{spec.caption}*")
    return BlockOut(
        origin="data", kind="table", columns=columns, rows=rows, data_key=spec.key, text="\n".join(lines)
    )


def data_blocks_for(case: EvalCase, section_id: str) -> list[BlockOut]:
    return [build_data_block(case, b) for b in case.section(section_id).data_blocks]


def render_markdown(title: str, sections: list[SectionOut]) -> str:
    """``# title``, then ``##`` per top-level section (``###`` and deeper by level), then blocks."""
    parts = [f"# {title}"]

    def walk(s: SectionOut) -> None:
        parts.append(f"{'#' * min(6, s.level + 1)} {s.title}")
        for b in s.blocks:
            if b.text.strip():
                parts.append(b.text.strip())

    for s in sections:
        walk(s)
    return "\n\n".join(parts) + "\n"


def finalize(result: GenerationResult) -> GenerationResult:
    result.markdown = render_markdown(result.title, result.sections)
    return result
