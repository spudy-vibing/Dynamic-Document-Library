"""N0 null baseline: titles and data blocks only, handled by every grader (EVALS_PROPOSAL §6.4)."""

from __future__ import annotations

import asyncio

import pytest

from evals.core.cases import load_case_set
from evals.core.render import render_markdown
from evals.generators.n0_null import NullBaseline
from evals.graders import ALL_GRADERS
from evals.graders.text import words

CASES = load_case_set("dev")


class NoLLM:
    """Any call fails: the null baseline must never touch the model."""

    calls: list = []

    async def complete(self, **kwargs):  # pragma: no cover - must not be reached
        raise AssertionError("null baseline called the LLM")


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_null_baseline(case):
    r = asyncio.run(NullBaseline().generate(case, NoLLM(), rep=2))
    assert (r.generator, r.generator_version, r.case_id, r.rep) == ("n0-null", "1", case.id, 2)
    assert r.calls == [] and r.error is None
    assert [s.id for s in r.sections] == [s.id for s in case.sections]
    assert all(b.origin == "data" for s in r.sections for b in s.blocks)
    assert sum(len(words(s.model_text())) for s in r.sections) == 0
    assert r.markdown == render_markdown(case.title, r.sections)

    s = {sc.metric: sc for g in ALL_GRADERS for sc in g.grade(case, r)}
    assert s["unr"].den == 0 and s["chatter"].den == 0 and s["rep_pairs"].den == 0
    assert s["structure"].value == 1.0
    assert s["data_exact"].value in (1.0, None)
    assert s["cost_usd"].value == 0.0
    assert s["length_ratio"].value == 0.0
    # every length or count constraint fails on an empty section; only forbidden phrases pass
    assert s["adherence"].value < 0.5
