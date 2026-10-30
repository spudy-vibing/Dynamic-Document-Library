"""EVAL-16: compare two runs with paired cluster-bootstrap deltas."""

from __future__ import annotations

import pytest

from evals.compare import compare_runs, is_regression, paired_bootstrap_delta, per_case, render_compare
from evals.core.config import load_config
from evals.core.storage import RunStore

CONFIG = load_config()


def test_per_case_pools_ratio_and_value_metrics():
    rows = [{"case_id": "a", "num": 1, "den": 4}, {"case_id": "a", "num": 1, "den": 6}, {"case_id": "b", "num": 0, "den": 5}]
    assert per_case(rows) == {"a": (2, 10), "b": (0, 5)}
    assert per_case([{"case_id": "a", "value": 2.0}, {"case_id": "a", "value": 4.0}]) == {"a": (6.0, 2.0)}


def test_paired_delta_deterministic_and_regression_rule():
    a = {f"c{i}": (1.0, 10.0) for i in range(8)}
    b = {f"c{i}": (3.0, 10.0) for i in range(8)}
    d1 = paired_bootstrap_delta(a, b, resamples=500, seed=3)
    assert d1 == paired_bootstrap_delta(a, b, resamples=500, seed=3)
    assert d1["delta"] == pytest.approx(0.2) and d1["ci"][0] > 0
    assert is_regression("unr", d1)  # lower is better and it rose with CI > 0
    assert not is_regression("structure", d1)  # higher is better: an improvement
    assert is_regression("trap.t1_derivable", d1)
    assert not is_regression("unknown_metric", d1)
    noisy = paired_bootstrap_delta({"c0": (0, 10), "c1": (5, 10)}, {"c0": (5, 10), "c1": (0, 10)}, resamples=500)
    assert noisy["ci"][0] < 0 < noisy["ci"][1] and not is_regression("unr", noisy)
    assert paired_bootstrap_delta({"x": (1, 1)}, {"y": (1, 1)}) is None


def _run(tmp_path, run_id, unr_num, jv="jv"):
    store = RunStore(run_id, tmp_path)
    store.open_manifest({"mode": "fake", "generators": [{"name": "b1-parallel", "version": "1"}], "judge_version": jv})
    store.append("scores", [
        {"generator": "b1-parallel", "case_id": f"c{i}", "rep": 1, "metric": "unr", "value": None, "num": unr_num, "den": 10}
        for i in range(6)
    ])
    return store


def test_compare_runs_flags_regression_and_judge_version_change(tmp_path):
    a, b = _run(tmp_path, "a", 1), _run(tmp_path, "b", 4, jv="other")
    result = compare_runs(a, b, CONFIG, resamples=300)
    (row,) = result["metrics"]
    assert row["regression"] and result["regressions"] == 1
    assert any("judge_version differs" in w for w in result["warnings"])
    text = render_compare(result)
    assert "**REGRESSION**" in text and "Regressions: **1**" in text
