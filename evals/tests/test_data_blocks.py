"""Data-block exactness against core.render.build_data_block."""

from __future__ import annotations

import pytest

from evals.core.cases import load_case_set
from evals.core.types import BlockOut
from evals.graders.data_blocks import DataBlocksGrader
from evals.perturb.samples import build_result


@pytest.fixture(scope="module")
def case():
    return next(c for c in load_case_set("dev") if c.id == "q-halcyra-1903")


def score(case, r):
    (s,) = DataBlocksGrader().grade(case, r)
    return s


def test_harness_blocks_are_exact(case):
    s = score(case, build_result(case, {}))
    assert (s.num, s.den, s.value) == (2, 2, 1.0)


def test_changed_cell(case):
    r = build_result(case, {})
    table = next(b for b in r.sections[1].blocks if b.kind == "table")
    table.rows[0][2] = "30,100,001"
    s = score(case, r)
    assert (s.num, s.den) == (1, 2) and s.detail["problems"][0]["problem"] == "cells differ"


def test_changed_metric_label(case):
    r = build_result(case, {})
    r.sections[0].blocks[0].label = "Revenue"
    assert score(case, r).num == 1


def test_missing_block(case):
    r = build_result(case, {})
    r.sections[0].blocks = []
    s = score(case, r)
    assert (s.num, s.den) == (1, 2) and s.detail["problems"][0]["problem"] == "missing"


def test_unexpected_block_counts_against(case):
    r = build_result(case, {})
    r.sections[2].blocks.append(BlockOut(origin="data", kind="metric", label="x", value="1", data_key="metrics.churn_pct", text=""))
    s = score(case, r)
    assert (s.num, s.den) == (2, 3)


def test_case_without_data_blocks():
    c = next(c for c in load_case_set("dev") if c.family == "f3_readme")
    s = score(c, build_result(c, {}))
    assert (s.num, s.den, s.value) == (0, 0, None)
