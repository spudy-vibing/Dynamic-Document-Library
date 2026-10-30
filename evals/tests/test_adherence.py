"""Constraint adherence and length ratio."""

from __future__ import annotations

import pytest

from evals.core.cases import load_case_set
from evals.core.types import CaseSection, Constraints
from evals.graders.adherence import AdherenceGrader, check_section
from evals.perturb.samples import build_result


def section(**c) -> CaseSection:
    return CaseSection(id="s", title="S", brief="", instruction="", constraints=Constraints(**c))


def met(sec, text):
    return {(c["constraint"], c["met"]) for c in check_section(sec, text, 0.10)}


def test_word_limits_with_tolerance():
    sec = section(max_words=10, min_words=10)
    assert met(sec, " ".join(["word"] * 11)) == {("max_words", True), ("min_words", True)}
    assert met(sec, " ".join(["word"] * 12)) == {("max_words", False), ("min_words", True)}
    assert met(sec, " ".join(["word"] * 8)) == {("max_words", True), ("min_words", False)}


def test_paragraphs_ignore_lists_and_code():
    sec = section(paragraphs=2)
    text = "First paragraph.\n\n- a list\n- b\n\n```\ncode\n```\n\nSecond paragraph."
    assert met(sec, text) == {("paragraphs", True)}
    assert met(sec, "Only one.") == {("paragraphs", False)}


def test_list_items():
    sec = section(list_items=3)
    assert met(sec, "- a\n- b\n- c") == {("list_items", True)}
    assert met(sec, "1. a\n2. b\n3. c\n4. d") == {("list_items", False)}


def test_forbidden_phrases_whole_words_case_insensitive():
    sec = section(forbidden_phrases=("synergy", "game-changer"))
    assert met(sec, "A Game-Changer for us.") == {("forbidden_phrase", True), ("forbidden_phrase", False)}
    assert met(sec, "Synergyless prose.") == {("forbidden_phrase", True)}


def test_empty_section_fails_length_constraints():
    sec = section(max_words=50, paragraphs=1, forbidden_phrases=("synergy",))
    assert met(sec, "") == {("max_words", False), ("paragraphs", False), ("forbidden_phrase", True)}


@pytest.fixture(scope="module")
def case():
    return next(c for c in load_case_set("dev") if c.id == "q-halcyra-1903")


def test_document_pooling_and_length_ratio(case):
    texts = {
        "summary": "One paragraph only.",  # paragraphs=2 fails; max_words ok
        "analysis": "First.\n\nSecond.",
        "risks": "- a\n- b\n- c",
        "recommendations": "- a\n- b\n- c",
    }
    adh, lr = AdherenceGrader().grade(case, build_result(case, texts))
    assert adh.metric == "adherence" and (adh.num, adh.den) == (12, 13)
    assert adh.detail["failed"]["summary"][0]["constraint"] == "paragraphs"
    words = [3, 2, 3, 3]
    maxes = [120, 200, 90, 100]
    assert lr.metric == "length_ratio" and lr.value == pytest.approx(sum(w / m for w, m in zip(words, maxes)) / 4)
