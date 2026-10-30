"""Structure score: H1s, heading levels, repeated titles, sections present and in order."""

from __future__ import annotations

import pytest

from evals.core.cases import load_case_set
from evals.core.render import finalize
from evals.core.types import BlockOut
from evals.graders.structure import StructureGrader
from evals.perturb.samples import build_result

TEXTS = {
    "summary": "Revenue fell in the quarter.",
    "analysis": "### Revenue\n\nRevenue declined.\n\n### Margin\n\nMargin narrowed.",
    "risks": "- Supplier risk.",
    "recommendations": "- Add a supplier.",
}


@pytest.fixture(scope="module")
def case():
    return next(c for c in load_case_set("dev") if c.id == "q-halcyra-1903")


def score(case, r):
    (s,) = StructureGrader().grade(case, r)
    return s


def test_clean_document_scores_one(case):
    s = score(case, build_result(case, TEXTS))
    assert s.value == 1.0 and s.detail["failed"] == {}


def test_extra_h1(case):
    s = score(case, build_result(case, {**TEXTS, "summary": "# Results\n\nRevenue fell."}))
    assert set(s.detail["failed"]) == {"no_extra_h1"} and s.value == pytest.approx(0.8)


def test_skipped_level(case):
    s = score(case, build_result(case, {**TEXTS, "analysis": "#### Revenue\n\nRevenue declined."}))
    assert "no_skipped_levels" in s.detail["failed"]


def test_heading_repeating_section_title(case):
    s = score(case, build_result(case, {**TEXTS, "risks": "### Risks\n\n- Supplier risk."}))
    assert "no_title_repeat" in s.detail["failed"]


def test_missing_and_error_sections(case):
    r = build_result(case, TEXTS)
    r.sections = [s for s in r.sections if s.id != "risks"]
    r.sections[0].blocks = [BlockOut(origin="error", text="generation failed")]
    finalize(r)
    s = score(case, r)
    assert s.detail["failed"]["sections_present"] == ["summary", "risks"]


def test_out_of_order(case):
    r = build_result(case, TEXTS)
    r.sections[1], r.sections[2] = r.sections[2], r.sections[1]
    s = score(case, finalize(r))
    assert set(s.detail["failed"]) == {"sections_ordered"}


def test_uses_rendered_markdown_when_empty(case):
    r = build_result(case, {**TEXTS, "summary": "# Results\n\nRevenue fell."})
    r.markdown = ""
    assert "no_extra_h1" in score(case, r).detail["failed"]


def test_heading_in_code_block_is_ignored(case):
    s = score(case, build_result(case, {**TEXTS, "summary": "Run:\n\n```bash\n# comment\n```"}))
    assert s.value == 1.0
