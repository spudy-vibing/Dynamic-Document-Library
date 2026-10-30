"""Chatter rate on first and last paragraphs."""

from __future__ import annotations

import pytest

from evals.core.cases import load_case_set
from evals.graders.chatter import ChatterGrader
from evals.perturb.samples import build_result

CLEAN = {
    "summary": "Revenue fell to $30.1M.\n\nMargin narrowed.",
    "analysis": "Revenue declined.\n\nCustomers fell.",
    "risks": "- Supplier risk.",
    "recommendations": "- Add a supplier.",
}


@pytest.fixture(scope="module")
def case():
    return next(c for c in load_case_set("dev") if c.id == "q-halcyra-1903")


def chatter(case, texts):
    (s,) = ChatterGrader().grade(case, build_result(case, texts))
    return s


def test_clean(case):
    s = chatter(case, CLEAN)
    assert (s.num, s.den, s.value) == (0, 4, 0.0)


@pytest.mark.parametrize(
    "section, text",
    [
        ("summary", "Sure! Here's the summary:\n\nRevenue fell."),
        ("summary", "Certainly. Revenue fell."),
        ("summary", "Of course, revenue fell."),
        ("summary", "Here is the executive summary.\n\nRevenue fell."),
        ("summary", "Below is an overview of results."),
        ("analysis", "Revenue declined.\n\nI hope this helps the leadership team."),
        ("analysis", "Revenue declined.\n\nLet me know if you want more detail."),
        ("analysis", "Revenue declined.\n\nFeel free to ask for a breakdown."),
        ("analysis", "As an AI, I cannot verify this. Revenue declined."),
        ("analysis", "In this section, we will look at revenue.\n\nRevenue declined."),
        ("analysis", "Revenue declined, as you asked me to cover."),
        ("analysis", "Revenue declined.\n\nIn conclusion, the quarter was weak."),
    ],
)
def test_lexicon(case, section, text):
    s = chatter(case, {**CLEAN, section: text})
    assert s.num == 1, s.detail


def test_in_conclusion_allowed_in_last_section(case):
    s = chatter(case, {**CLEAN, "recommendations": "- Add a supplier.\n\nIn conclusion, act early."})
    assert s.num == 0


def test_middle_paragraphs_are_not_checked(case):
    s = chatter(case, {**CLEAN, "analysis": "Revenue declined.\n\nSure enough, costs rose.\n\nCustomers fell."})
    assert s.num == 0


def test_plain_here_is_not_flagged_mid_sentence(case):
    s = chatter(case, {**CLEAN, "analysis": "The risk here is supplier concentration."})
    assert s.num == 0
