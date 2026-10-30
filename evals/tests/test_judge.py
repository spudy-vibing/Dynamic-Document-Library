"""EVAL-9 / EVAL-12: pairwise judge logic, statistics and the Batches path (stubbed)."""

from __future__ import annotations

import asyncio
import json

import pytest

from evals.core.config import load_config
from evals.core.llm import FakeLLM
from evals.core.storage import RunStore
from evals.generators import GENERATORS
from evals.judge import pairwise as pw
from evals.tests.fakes import StubBatches, StubClient, stub_message, write_tiny_case

CONFIG = load_config()
JUDGE = CONFIG["models"]["judge"]


def verdict_json(**v):
    return json.dumps({q: {"evidence": ["A: x"], "verdict": v.get(q, "tie")} for q in pw.QUESTIONS})


def row(order, label_a, label_b, case="c1", rep=1, pair=("x", "y"), **verdicts):
    return {"custom_id": f"{case}-{rep}-{order}", "case_id": case, "rep": rep, "pair": list(pair), "order": order,
            "label_a": label_a, "label_b": label_b, "status": "ok", "words": {"x": 100, "y": 80},
            "verdicts": {q: verdicts.get(q, "tie") for q in pw.QUESTIONS}}


def test_judge_version_includes_rubric_hash():
    jv = pw.judge_version(CONFIG)
    assert jv.startswith(CONFIG["judge"]["version"] + "-") and jv.endswith(pw.rubric().sha[:8])


def test_parse_verdicts_variants():
    v, ev = pw.parse_verdicts("Here:\n```json\n" + verdict_json(overall="A", q1_coherence="b") + "\n```")
    assert v["overall"] == "A" and v["q1_coherence"] == "B" and v["q2_redundancy"] == "tie"
    assert ev["overall"] == ["A: x"]
    with pytest.raises(ValueError):
        pw.parse_verdicts('{"overall": {"verdict": "A"}}')  # missing questions
    with pytest.raises(ValueError):
        pw.parse_verdicts("no json")


def test_win_requires_both_orders_to_agree():
    # Order 1 shows x as A, order 2 shows y as A. x wins overall in both orders.
    rows = [row(1, "x", "y", overall="A", q1_coherence="A"), row(2, "y", "x", overall="B", q1_coherence="A")]
    (o,) = pw.combine(rows)
    assert o.winners["overall"] == "x"
    assert o.winners["q1_coherence"] is None  # position-biased "A" both times -> tie
    assert o.winners["q2_redundancy"] is None
    assert o.score_x("overall") == 1.0 and o.score_x("q1_coherence") == 0.5


def test_tie_in_one_order_is_tie_and_incomplete_pairs_dropped():
    rows = [row(1, "x", "y", overall="A"), row(2, "y", "x", overall="tie")]
    assert pw.combine(rows)[0].winners["overall"] is None
    assert pw.combine([row(1, "x", "y", overall="A")]) == []
    bad = row(2, "y", "x", overall="B")
    bad["status"] = "parse_error"
    assert pw.combine([row(1, "x", "y", overall="A"), bad]) == []


def test_win_rate_and_bootstrap_determinism():
    rows = []
    for i, (v1, v2) in enumerate([("A", "B"), ("A", "B"), ("B", "A"), ("A", "A")]):
        rows += [row(1, "x", "y", case=f"c{i}", overall=v1), row(2, "y", "x", case=f"c{i}", overall=v2)]
    outcomes = pw.combine(rows)
    r1 = pw.win_rate(outcomes, "overall", resamples=2000, seed=7)
    r2 = pw.win_rate(outcomes, "overall", resamples=2000, seed=7)
    assert r1 == r2
    assert (r1["wins"], r1["ties"], r1["losses"], r1["pairs"]) == (2, 1, 1, 4)
    assert r1["win_rate"] == pytest.approx((2 + 0.5) / 4)
    lo, hi = r1["ci"]
    assert 0 <= lo <= r1["win_rate"] <= hi <= 1
    assert pw.win_rate(outcomes, "overall", resamples=2000, seed=8)["ci"] is not None


def test_bootstrap_ci_is_cluster_level():
    # One case with many pairs must not shrink the CI as if pairs were independent.
    assert pw.ratio_bootstrap_ci([(10, 10)], resamples=100) == (1.0, 1.0)
    lo, hi = pw.ratio_bootstrap_ci([(10, 10), (0, 10)], resamples=2000, seed=1)
    assert lo == 0.0 and hi == 1.0


def test_length_bias_flag():
    outs = [pw.PairOutcome(f"c{i}", 1, "x", "y", {"overall": w}, {"x": 100 + d, "y": 100})
            for i, (w, d) in enumerate([("x", 50), ("x", 40), ("y", -30), (None, 0), ("y", -60)])]
    lb = pw.length_bias(outs)
    assert lb["r"] > 0.9 and lb["flag"]
    assert pw.length_bias(outs[:2])["r"] is None


@pytest.fixture
def run_with_docs(tmp_path):
    case = write_tiny_case(tmp_path / "cases")
    store = RunStore("j1", tmp_path / "runs")
    for name in ("b0-oneshot", "b1-parallel"):
        for rep in (1, 2):
            r = asyncio.run(GENERATORS[name].generate(case, FakeLLM("claude-sonnet-5-5"), rep))
            store.save_result(r)
    return case, store


def test_plan_pairs_anonymised_and_skips_errors(run_with_docs):
    case, store = run_with_docs
    broken = store.load_result("b1-parallel", case.id, 2)
    broken.error = "infra: x"
    store.save_result(broken)
    tasks, skipped = pw.plan_pairs(store.iter_results(), [["b0-oneshot", "b1-parallel"]], seed="s")
    assert [(t.case_id, t.rep) for t in tasks] == [(case.id, 1)] and len(skipped) == 1
    t = tasks[0]
    assert t.labels(1) == tuple(reversed(t.labels(2)))
    system, messages = pw.build_request(case, t.docs["b0-oneshot"], t.docs["b1-parallel"])
    blob = system + json.dumps(messages)
    assert "b0-oneshot" not in blob and "b1-parallel" not in blob and "oneshot" not in blob
    assert messages[0]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert case.purpose in blob and "<document_a>" in blob


def test_fake_judge_end_to_end_and_resume(run_with_docs):
    case, store = run_with_docs
    tasks, _ = pw.plan_pairs(store.iter_results(), [["b0-oneshot", "b1-parallel"]], seed="s")
    jv = pw.judge_version(CONFIG)
    jobs = pw.pending_jobs(tasks, {case.id: case}, set())
    asyncio.run(pw.judge_with_llm(jobs, pw.make_fake_judge(CONFIG), store, jv))
    rows = store.read("judge")
    assert len(rows) == 4 and all(r["status"] == "ok" for r in rows)
    assert {r["order"] for r in rows} == {1, 2}
    assert all(c["phase"] == "judge" and c["requested_model"] == JUDGE for c in store.read("calls"))
    done = {r["custom_id"] for r in rows}
    assert pw.pending_jobs(tasks, {case.id: case}, done) == []
    summary = pw.summarise(rows, [["b0-oneshot", "b1-parallel"]], resamples=500)
    assert summary[0]["questions"]["overall"]["pairs"] == 2


def test_batches_map_by_custom_id_cost_factor_and_refusal_retry(run_with_docs):
    case, store = run_with_docs
    tasks, _ = pw.plan_pairs(store.iter_results(), [["b0-oneshot", "b1-parallel"]], seed="s")
    jobs = pw.pending_jobs(tasks, {case.id: case}, set())
    refuse_once = {jobs[0].custom_id}
    errored_once = {jobs[1].custom_id}
    seen: dict[str, int] = {}

    def responder(custom_id, params):
        seen[custom_id] = seen.get(custom_id, 0) + 1
        assert params["output_config"] == {"effort": CONFIG["effort"]["judge"]}
        assert not {"temperature", "top_p", "top_k", "fallbacks", "thinking"} & set(params)
        if custom_id in errored_once and seen[custom_id] == 1:
            return None
        if custom_id in refuse_once and seen[custom_id] == 1:
            return stub_message("", model=JUDGE, stop_reason="refusal", category="cyber")
        # Encode which document is A, so a position mix-up would show in the verdicts.
        text = params["messages"][0]["content"][1]["text"]
        a_len = len(text.split("<document_b>")[0])
        b_len = len(text.split("<document_b>")[1])
        winner = "A" if a_len >= b_len else "B"
        return stub_message(verdict_json(overall=winner), model=JUDGE, input_tokens=1000, output_tokens=100)

    batches = StubBatches(responder, polls_before_end=2)
    client = StubClient(batches=batches)
    slept = []

    async def sleep(s):
        slept.append(s)

    jv = pw.judge_version(CONFIG)
    asyncio.run(pw.judge_with_batches(jobs, client, CONFIG, store, jv, poll_s=0.01, sleep=sleep))
    assert len(batches.created) == 2  # second round: the refused and the errored item
    assert sorted(r["custom_id"] for r in batches.created[1]) == sorted(refuse_once | errored_once)
    rows = store.read("judge")
    assert len(rows) == 4 and all(r["status"] == "ok" for r in rows)
    for r in rows:  # mapped by custom_id even though results came back reversed
        job = next(j for j in jobs if j.custom_id == r["custom_id"])
        assert (r["label_a"], r["label_b"]) == job.task.labels(job.order)
    calls = [c for c in store.read("calls") if c["phase"] == "judge"]
    assert len(calls) == 5  # 4 successes + 1 recorded refusal
    full = (1000 * CONFIG["prices"][JUDGE]["input"] + 100 * CONFIG["prices"][JUDGE]["output"]) / 1e6
    ok_costs = [c["cost_usd"] for c in calls if c["status"] == "ok"]
    assert all(c == pytest.approx(full * 0.5) for c in ok_costs)
    assert [e["kind"] for e in store.read("errors")] == ["refusal"]
    assert slept  # polled until ended


def test_batches_refused_twice_is_error_row(run_with_docs):
    case, store = run_with_docs
    tasks, _ = pw.plan_pairs(store.iter_results(), [["b0-oneshot", "b1-parallel"]], seed="s")
    jobs = pw.pending_jobs(tasks, {case.id: case}, set())[:1]
    batches = StubBatches(lambda cid, p: stub_message("", model=JUDGE, stop_reason="refusal", category="bio"), 0)

    async def sleep(s):
        return None

    asyncio.run(pw.judge_with_batches(jobs, StubClient(batches=batches), CONFIG, store, "jv", sleep=sleep))
    (r,) = store.read("judge")
    assert r["status"] == "refusal" and len(batches.created) == 2


def test_batches_served_model_mismatch_not_scored(run_with_docs):
    case, store = run_with_docs
    tasks, _ = pw.plan_pairs(store.iter_results(), [["b0-oneshot", "b1-parallel"]], seed="s")
    jobs = pw.pending_jobs(tasks, {case.id: case}, set())[:1]
    batches = StubBatches(lambda cid, p: stub_message(verdict_json(), model="claude-other"), 0)

    async def sleep(s):
        return None

    asyncio.run(pw.judge_with_batches(jobs, StubClient(batches=batches), CONFIG, store, "jv", sleep=sleep))
    (r,) = store.read("judge")
    assert r["status"] == "infra" and pw.combine([r]) == []


def test_batches_collects_earlier_submitted_batch(run_with_docs):
    """An interrupted run's submitted batch is collected on restart, not paid for twice."""
    case, store = run_with_docs
    tasks, _ = pw.plan_pairs(store.iter_results(), [["b0-oneshot", "b1-parallel"]], seed="s")
    jobs = pw.pending_jobs(tasks, {case.id: case}, set())
    batches = StubBatches(lambda cid, p: stub_message(verdict_json(overall="A"), model=JUDGE), 0)
    client = StubClient(batches=batches)
    asyncio.run(batches.create(requests=[{"custom_id": j.custom_id, "params": {}} for j in jobs]))
    store.append("judge_batches", [{"batch_id": "msgbatch_1", "event": "submitted", "custom_ids": [j.custom_id for j in jobs]}])

    async def sleep(s):
        return None

    asyncio.run(pw.judge_with_batches(jobs, client, CONFIG, store, "jv", sleep=sleep))
    assert len(batches.created) == 1
    assert len(store.read("judge")) == 4
