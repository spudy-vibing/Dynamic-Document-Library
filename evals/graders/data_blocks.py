"""Data-block exactness (EVAL-8, EVAL-22, §6.1).

For every DataBlockSpec in the case outline, the expected block is what
``core.render.build_data_block`` produces from data.json. The result's section must hold
a ``data``-origin block with the same ``data_key`` and kind whose cells are equal:
columns and rows for a table, label and value for a metric (strings, compared exactly).

Metric: data_exact, num = expected blocks present and exact,
den = expected blocks + unexpected data blocks (a data block the outline does not ask for
counts against the score). None when the case has no data blocks and none were produced.
"""

from __future__ import annotations

from typing import Any

from evals.core.render import build_data_block
from evals.core.types import BlockOut, EvalCase, GenerationResult, Score


def _cells(b: BlockOut) -> Any:
    if b.kind == "table":
        return {"columns": list(b.columns or []), "rows": [list(r) for r in (b.rows or [])]}
    return {"label": b.label, "value": b.value}


class DataBlocksGrader:
    name = "data_blocks"
    version = "1"

    def grade(self, case: EvalCase, result: GenerationResult) -> list[Score]:
        if result.error:
            return []
        by_id = {s.id: s for s in result.sections}
        exact = expected = 0
        problems: list[dict[str, Any]] = []
        claimed: set[int] = set()
        for cs in case.sections:
            got_blocks = [b for b in by_id[cs.id].blocks if b.origin == "data"] if cs.id in by_id else []
            for spec in cs.data_blocks:
                expected += 1
                want = build_data_block(case, spec)
                got = next((b for b in got_blocks if b.data_key == spec.key and b.kind == want.kind and id(b) not in claimed), None)
                if got is None:
                    problems.append({"section": cs.id, "key": spec.key, "problem": "missing"})
                    continue
                claimed.add(id(got))
                if _cells(got) == _cells(want):
                    exact += 1
                else:
                    problems.append({"section": cs.id, "key": spec.key, "problem": "cells differ",
                                     "expected": _cells(want), "got": _cells(got)})
        extra = [
            {"section": s.id, "key": b.data_key, "problem": "unexpected"}
            for s in result.sections
            for b in s.blocks
            if b.origin == "data" and id(b) not in claimed
        ]
        den = expected + len(extra)
        return [Score("data_exact", exact / den if den else None, exact, den, {"problems": problems + extra})]
