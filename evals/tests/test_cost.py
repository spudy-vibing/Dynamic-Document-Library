"""Cost, cache hit rate and wall time from call records."""

from __future__ import annotations

import pytest

from evals.core.cases import load_case_set
from evals.core.types import CallRecord
from evals.graders.cost import CostGrader
from evals.perturb.samples import build_result


def call(cost: float | None, inp: int, read: int, write: int) -> CallRecord:
    return CallRecord(
        role="generate", node_id="s", status="ok", requested_model="claude-sonnet-5-5",
        served_model="claude-sonnet-5-5", effort="medium", stop_reason="end_turn", refusal_category=None,
        input_tokens=inp, output_tokens=500, cache_read_tokens=read, cache_write_tokens=write,
        latency_ms=1000, attempts=1, cost_usd=cost,
    )


@pytest.fixture(scope="module")
def case():
    return load_case_set("dev")[0]


def grade(case, calls, wall=1234):
    r = build_result(case, {})
    r.calls, r.wall_ms = calls, wall
    return {s.metric: s for s in CostGrader().grade(case, r)}


def test_sums_and_pools(case):
    s = grade(case, [call(0.01, 100, 0, 900), call(0.02, 100, 900, 0)])
    assert s["cost_usd"].value == pytest.approx(0.03)
    assert (s["cache_hit"].num, s["cache_hit"].den) == (900, 2000)
    assert s["cache_hit"].value == pytest.approx(0.45)
    assert s["wall_ms"].value == 1234


def test_unpriced_call_makes_cost_unknown(case):
    s = grade(case, [call(0.01, 100, 0, 0), call(None, 100, 0, 0)])
    assert s["cost_usd"].value is None


def test_no_calls(case):
    s = grade(case, [])
    assert s["cost_usd"].value == 0.0 and s["cache_hit"].value is None and s["cache_hit"].den == 0


def test_error_result_is_not_scored(case):
    r = build_result(case, {})
    r.error = "infra"
    assert CostGrader().grade(case, r) == []
