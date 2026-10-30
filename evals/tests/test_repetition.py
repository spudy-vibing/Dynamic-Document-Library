"""Cross-section repetition (XREP, REP-pairs)."""

from __future__ import annotations

import pytest

from evals.core.cases import load_case_set
from evals.graders.repetition import RepetitionGrader, content_words, ngrams, overlap
from evals.perturb.samples import build_result


@pytest.fixture(scope="module")
def case():
    return next(c for c in load_case_set("dev") if c.id == "q-halcyra-1903")


def grade(case, texts):
    return {s.metric: s for s in RepetitionGrader().grade(case, build_result(case, texts))}


def test_content_words_drop_stop_words_and_punctuation():
    assert content_words("The team's revenue, in Q3, was UP!") == ["teams", "revenue", "q3"]


def test_overlap_formula():
    a = ngrams("a1 b1 c1 d1 e1 f1 g1".split(), 6)  # 2 grams
    b = ngrams("x0 a1 b1 c1 d1 e1 f1".split(), 6)  # 2 grams, 1 shared
    assert overlap(a, b) == 0.5
    assert overlap(a, set()) == 0.0


def test_distinct_sections_score_zero(case):
    s = grade(case, {
        "summary": "Freight volumes dropped sharply after the supplier outage hit northern routes.",
        "analysis": "Legacy line discounting reduced realised prices across every regional contract.",
        "risks": "- Carrier capacity remains tight heading into winter.",
    })
    assert s["xrep"].value == 0.0
    assert (s["rep_pairs"].num, s["rep_pairs"].den) == (0, 3)


def test_shared_passage_is_detected(case):
    shared = "Freight volumes dropped sharply after the supplier outage hit northern routes during August."
    s = grade(case, {
        "summary": shared,
        "analysis": "Legacy discounting cut prices. " + shared,
        "risks": "- Carrier capacity remains tight heading into winter.",
    })
    assert s["xrep"].value > 0
    assert (s["rep_pairs"].num, s["rep_pairs"].den) == (1, 3)
    pair = s["xrep"].detail["pairs"][0]
    assert {pair["a"], pair["b"]} == {"summary", "analysis"} and pair["overlap"] == 1.0


def test_fewer_than_two_model_sections(case):
    s = grade(case, {"summary": "Only one section has text in this document at all today."})
    assert s["xrep"].value is None and s["rep_pairs"].den == 0
