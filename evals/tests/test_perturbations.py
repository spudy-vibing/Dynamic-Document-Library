"""Perturbation suite (EVAL-14, EVALS_PROPOSAL §6.4).

Clean, hand-written outputs for four of the six dev cases live in tests/data/samples/.
They must score clean, and every deterministic grader must catch its scripted defect on
every sample (100% detection).
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from evals.core.cases import load_case_set
from evals.core.types import Score
from evals.graders import ALL_GRADERS
from evals.perturb import PERTURBATIONS, load_sample
from evals.perturb.perturbations import scale_figure_x10, shift_figure

SAMPLES = sorted((Path(__file__).parent / "data" / "samples").glob("*.json"))
CASES = {c.id: c for c in load_case_set("dev")}


def scores(case, result) -> dict[str, Score]:
    return {s.metric: s for g in ALL_GRADERS for s in g.grade(case, result)}


@pytest.fixture(scope="module", params=SAMPLES, ids=lambda p: p.stem)
def sample(request):
    case = CASES[request.param.stem]
    result = load_sample(request.param, case)
    return case, result, scores(case, result)


def test_enough_samples():
    assert len(SAMPLES) >= 3
    assert len({CASES[p.stem].family for p in SAMPLES}) == 3


def test_samples_score_clean(sample):
    case, _, s = sample
    assert s["unr"].num == 0 and s["unr"].den > 0, s["unr"].detail
    assert s["structure"].value == 1.0, s["structure"].detail
    assert s["chatter"].num == 0
    assert s["adherence"].value == 1.0, s["adherence"].detail
    assert s["xrep"].value == 0.0
    assert s["data_exact"].value in (1.0, None)
    if any(sec.data_blocks for sec in case.sections):
        assert s["data_exact"].value == 1.0
    for metric, sc in s.items():
        if metric.startswith("trap."):
            assert sc.num == 0, (metric, sc.detail)


def _needs_data_blocks(name: str) -> bool:
    return name in ("drop_data_block", "alter_cell")


@pytest.mark.parametrize("name", list(PERTURBATIONS))
def test_each_perturbation_is_detected(sample, name):
    case, clean, base = sample
    fn, metric = PERTURBATIONS[name]
    if _needs_data_blocks(name) and not any(sec.data_blocks for sec in case.sections):
        pytest.skip("case has no data blocks")
    perturbed = fn(clean, case)
    assert perturbed is not clean and perturbed.markdown != clean.markdown
    after = scores(case, perturbed)
    lower_is_worse = metric in ("structure", "adherence", "data_exact")
    if lower_is_worse:
        assert after[metric].value < base[metric].value, after[metric].detail
    else:
        assert after[metric].value > base[metric].value, after[metric].detail
    if metric == "xrep":
        assert after["rep_pairs"].num > base["rep_pairs"].num
    if metric == "unr":
        assert after["docs_unsupported"].value == 1.0 and after["unr_silent"].num == after["unr"].num


def test_perturbations_do_not_mutate_input(sample):
    case, clean, _ = sample
    before = clean.markdown
    for name, (fn, _) in PERTURBATIONS.items():
        if _needs_data_blocks(name) and not any(sec.data_blocks for sec in case.sections):
            continue
        fn(clean, case)
    assert clean.markdown == before


def test_unit_slip_trips_t3():
    unit_samples = [p for p in SAMPLES if CASES[p.stem].traps.unit]
    assert unit_samples
    for p in unit_samples:
        case = CASES[p.stem]
        clean = load_sample(p, case)
        for key in case.traps.unit:
            s = scores(case, scale_figure_x10(clean, case, key=key))
            assert s["trap.t3_unit"].num == 1, s["trap.t3_unit"].detail
            # +7% on the same figure is unsupported but not a unit slip
            s = scores(case, shift_figure(clean, case, key=key))
            assert s["unr"].num == 1 and s["trap.t3_unit"].num == 0


def test_spin_in_bad_news_sample():
    p = next(p for p in SAMPLES if CASES[p.stem].traps.bad_news)
    case = CASES[p.stem]
    r = load_sample(p, case)
    r.sections[0].blocks[0].text += " Even so, the business retains strong momentum."
    assert scores(case, r)["trap.t5_spin"].num == 1


def test_error_results_produce_no_scores(sample):
    case, clean, _ = sample
    r = copy.deepcopy(clean)
    r.error = "refusal (cyber)"
    for g in ALL_GRADERS:
        assert g.grade(case, r) == []
