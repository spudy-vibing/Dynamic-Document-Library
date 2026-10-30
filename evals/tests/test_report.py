"""Report aggregation and rendering (EVALS_PROPOSAL §6.5, EVAL-20, EVAL-21)."""

from __future__ import annotations

import json

import pytest

from evals.core.config import load_config
from evals.core.storage import RunStore
from evals.core.types import GenerationResult
from evals.report import aggregate, build_summary, render_report, write_report

CONFIG = load_config()


def test_aggregate_pools_ratios_and_means_values():
    pooled = aggregate([{"num": 1, "den": 10, "value": 0.1}, {"num": 9, "den": 10, "value": 0.9}, {"num": 0, "den": 80, "value": 0.0}])
    assert pooled["value"] == pytest.approx(10 / 100) and pooled["n"] == 3
    mean = aggregate([{"value": 1.0}, {"value": 3.0}, {"value": None}])
    assert mean == {"value": 2.0, "n": 2}
    assert aggregate([])["value"] is None


def _store(tmp_path, mode="fake", refusing=0):
    store = RunStore("r1", tmp_path / "runs")
    store.open_manifest({"mode": mode, "generators": [{"name": "g1", "version": "1"}, {"name": "g2", "version": "1"}],
                         "case_ids": ["c1"], "case_paths": [], "reps": 1, "judge_version": "jv",
                         "models": CONFIG["models"], "effort": CONFIG["effort"]})
    for g in ("g1", "g2"):
        for rep in range(1, 11):
            store.save_result(GenerationResult(g, "1", "c1", rep, "T", [], [], 10))
    store.append("scores", [
        {"generator": "g1", "case_id": "c1", "rep": 1, "metric": "unr", "value": 0.5, "num": 1, "den": 2},
        {"generator": "g1", "case_id": "c1", "rep": 2, "metric": "unr", "value": 0.0, "num": 0, "den": 8},
        {"generator": "g2", "case_id": "c1", "rep": 1, "metric": "trap.t1_derivable", "value": 1.0, "num": 1, "den": 1},
    ])
    store.append("calls", [
        {"phase": "generate", "generator": "g1", "case_id": "c1", "rep": 1, "cost_usd": 0.25, "input_tokens": 10,
         "output_tokens": 5, "cache_read_tokens": 0, "cache_write_tokens": 0, "stop_reason": "max_tokens"},
        {"phase": "judge", "case_id": "c1", "rep": 1, "cost_usd": 0.10, "input_tokens": 1, "output_tokens": 1,
         "cache_read_tokens": 0, "cache_write_tokens": 0},
    ])
    store.append("errors", [{"phase": "generate", "kind": "refusal", "generator": "g2", "case_id": "c1", "rep": r}
                            for r in range(1, refusing + 1)])
    return store


def test_summary_and_report(tmp_path):
    store = _store(tmp_path, refusing=1)
    s = build_summary(store, CONFIG)
    assert s["metrics"]["unr"]["g1"]["value"] == pytest.approx(0.1)
    assert "trap.t1_derivable" in s["traps"] and "trap.t1_derivable" not in s["metrics"]
    assert s["refusals"]["g2"]["refusal_rate"] == pytest.approx(0.1)
    assert s["refusals"]["g2"]["comparable"] is False  # 10% > 2%
    assert s["refusals"]["g1"]["max_tokens_stops"] == 1
    assert s["cost"]["generation_usd"] == pytest.approx(0.25) and s["cost"]["judge"]["usd"] == pytest.approx(0.10)
    text = render_report(s)
    assert "FAKE MODE — not a real result" in text
    assert "g2 (not comparable)" in text and "Not comparable" in text
    assert "| UNR | lower | 10.0% (n=2) | — |" in text
    assert "| t1_derivable |" in text
    assert "Generation $0.2500 · judging $0.1000" in text


def test_live_report_has_no_fake_banner_and_writes_scoreboard(tmp_path):
    store = _store(tmp_path, mode="live")
    report, summary = write_report(store, CONFIG, scoreboard_dir=tmp_path / "sb")
    assert "FAKE MODE" not in report.read_text()
    data = json.loads(summary.read_text())
    assert data["mode"] == "live" and data["run_id"] == "r1"
    assert (tmp_path / "sb" / "r1.md").read_text() == report.read_text()
