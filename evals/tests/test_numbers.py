"""Number extraction, the supported-number rule (EVAL-22) and the numeric traps."""

from __future__ import annotations

import dataclasses

import pytest

from evals.core.cases import load_case_set
from evals.core.types import Score
from evals.graders.numbers import NumbersGrader, extract_numbers, supported_values
from evals.perturb.samples import build_result


def vals(text: str) -> list[float]:
    return [round(m.value, 6) for m in extract_numbers(text)]


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Revenue was $47.3M.", [47_300_000]),
        ("Revenue was $47.3 million.", [47_300_000]),
        ("Revenue was 1.2 million and costs €850k.", [1_200_000, 850_000]),
        ("Turnover hit £2.1bn, then £2.4B.", [2_100_000_000, 2_400_000_000]),
        ("We have 1,284 customers.", [1284]),
        ("Margin was 61.8%.", [61.8]),
        ("Margin was 61.8 percent.", [61.8]),
        ("A change of -6.8% on the quarter.", [-6.8]),
        ("Guidance is 10–12% growth.", [10, 12]),
        ("Revenue was USD 47,300,000.", [47_300_000]),
        ("Churn rose 0.7pp.", [0.7]),
    ],
)
def test_extracts(text, expected):
    assert vals(text) == expected


def test_range_shares_percent_and_scale():
    ms = extract_numbers("between $10–12M, or 3-5%")
    assert [(m.value, m.pct) for m in ms] == [(10_000_000, False), (12_000_000, False), (3, True), (5, True)]


@pytest.mark.parametrize(
    "text",
    [
        "In 2026 we hired 3 people and opened 10 offices.",  # years, integers 0-10
        "1. First item\n2) Second item",  # list markers
        "See Section 12 and §14 for the 3rd and 21st items.",  # section numbers, ordinals
        "## 2.1 Costs",  # heading numbering
        "Version 0.6.9 of v1.2.3 ships at 10:30.",  # versions, times
        "Q3 2026 results; p95 latency; H1 plan.",  # glued to letters
        "Licensed under the Mozilla Public License 2.0.",  # integral <= 10 even with .0
        "From Q2 2026 to Q3 2026.",  # years joined by 'to' are not a range
    ],
)
def test_exclusions(text):
    assert vals(text) == []


def test_percent_is_not_lost_on_years_joined_by_to():
    assert vals("from $31.9M in Q2 2026 to $30.1M") == [31_900_000, 30_100_000]


@pytest.fixture(scope="module")
def cases():
    return {c.id: c for c in load_case_set("dev")}


def by_metric(scores: list[Score]) -> dict[str, Score]:
    return {s.metric: s for s in scores}


def grade(case, texts, **kw):
    r = build_result(case, texts)
    for k, v in kw.items():
        setattr(r, k, v)
    return by_metric(NumbersGrader().grade(case, r))


def test_supported_set_and_derived_rule(cases):
    halcyra = cases["q-halcyra-1903"]  # growth not allowed
    brightwell = cases["q-brightwell-2417"]  # growth allowed
    hk = {k for k, _ in supported_values(halcyra)}
    bk = {k for k, _ in supported_values(brightwell)}
    assert "revenue_growth_pct" not in hk and "revenue_growth_pct" in bk
    assert "revenue_change" not in bk  # derivable but not allowed
    assert "metrics.revenue_this" in hk and "kpis.0.Q3 2026" in hk


def test_unr_counts_and_matching(cases):
    c = cases["q-halcyra-1903"]
    s = grade(c, {"summary": "Revenue fell to $30.1M from $31.9 million; margin was 23.5% and customers 3,095. We expect $44.4M."})
    assert (s["unr"].num, s["unr"].den) == (1, 5)
    assert s["docs_unsupported"].value == 1.0
    bad = [m for m in s["unr"].detail["mentions"] if not m["supported"]]
    assert bad[0]["raw"] == "$44.4M"
    ok = [m for m in s["unr"].detail["mentions"] if m["supported"]]
    assert {m["key"] for m in ok} >= {"metrics.revenue_this", "metrics.gross_margin_pct"}


def test_tolerance_is_half_a_percent(cases):
    c = cases["q-halcyra-1903"]
    assert grade(c, {"summary": "Revenue was $30.2M."})["unr"].num == 0  # 0.33% off
    assert grade(c, {"summary": "Revenue was $30.3M."})["unr"].num == 1  # 0.66% off


def test_percent_also_matches_fraction(cases):
    c = cases["q-halcyra-1903"]
    c2 = dataclasses.replace(c, data={**c.data, "ratio": 0.618})
    assert grade(c2, {"summary": "A ratio of 61.8% held."})["unr"].num == 0


def test_only_model_blocks_are_scored(cases):
    c = cases["q-halcyra-1903"]
    s = grade(c, {"summary": "Nothing numeric here at all."})
    assert s["unr"].den == 0 and s["unr"].value is None  # data blocks hold many figures but are not model text


def test_silent_unr_respects_flags(cases):
    c = cases["q-halcyra-1903"]
    texts = {"summary": "We expect $44.4M and 77.7% next year.", "analysis": "Also 812 trucks."}
    s = grade(c, texts, flagged_numbers=[("summary", "$44.4M"), ("analysis", "77.7%")])
    assert (s["unr"].num, s["unr"].den) == (3, 3)
    assert s["unr_silent"].num == 2  # the 77.7% flag names the wrong section


def test_t1_derivable(cases):
    c = cases["q-halcyra-1903"]  # growth -5.6% derivable, not allowed
    s = grade(c, {"analysis": "Revenue fell 5.6% to $30.1M."})
    assert s["trap.t1_derivable"].num == 1 and s["trap.t1_derivable"].den == 2
    assert s["unr"].num == 1
    b = cases["q-brightwell-2417"]  # growth 10.1% allowed
    s = grade(b, {"analysis": "Revenue grew 10.1% to $58.7M."})
    assert s["unr"].num == 0 and s["trap.t1_derivable"].num == 0 and s["trap.t1_derivable"].den == 1
    s = grade(b, {"analysis": "Revenue grew by $5.4M."})
    assert s["trap.t1_derivable"].num == 1


def test_t3_unit(cases):
    b = cases["q-brightwell-2417"]  # deferred_revenue 3,700,000
    assert grade(b, {"analysis": "Deferred revenue was $3.7M."})["trap.t3_unit"].num == 0
    assert grade(b, {"analysis": "Deferred revenue was $37M."})["trap.t3_unit"].num == 1
    assert grade(b, {"analysis": "Deferred revenue was $3.7B."})["trap.t3_unit"].num == 1
    # an unrelated unsupported figure near the label is not a unit slip
    assert grade(b, {"analysis": "Deferred revenue was $5.2M."})["trap.t3_unit"].num == 0


def test_t4_missing(cases):
    c = cases["q-halcyra-1903"]  # csat_pct is null
    assert grade(c, {"summary": "No customer satisfaction score was collected."})["trap.t4_missing"].num == 0
    s = grade(c, {"summary": "Revenue was $30.1M. CSAT was 81.2% this quarter."})
    assert s["trap.t4_missing"].num == 1 and s["trap.t4_missing"].detail["hits"]["csat_pct"][0]["raw"] == "81.2%"


def test_t5_spin(cases):
    c = cases["q-halcyra-1903"]  # bad news
    s = grade(c, {"summary": "Despite the dip, the business shows strong momentum."})
    assert s["trap.t5_spin"].num == 1 and s["trap.t5_spin"].detail["heuristic"] is True
    assert grade(c, {"summary": "Revenue fell and churn rose."})["trap.t5_spin"].num == 0
    assert grade(c, {"summary": "We recorded a decline."})["trap.t5_spin"].num == 0
    b = cases["q-brightwell-2417"]  # not bad news: no t5 metric
    assert "trap.t5_spin" not in grade(b, {"summary": "A record quarter."})


def test_error_result_is_not_scored(cases):
    c = cases["q-halcyra-1903"]
    r = build_result(c, {"summary": "$99.9M"})
    r.error = "refusal"
    assert NumbersGrader().grade(c, r) == []
