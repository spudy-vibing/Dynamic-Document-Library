"""EVAL-10 / EVAL-12 / EVAL-21: absolute rubric judge, its rows in report and compare, and the CLI wiring."""

from __future__ import annotations

import asyncio
import copy
import json

import pytest

from evals import runner
from evals.compare import compare_runs
from evals.core.config import load_config
from evals.core.llm import FakeLLM, FakeRefusal, build_params
from evals.core.storage import RunStore
from evals.generators import GENERATORS
from evals.judge import absolute as ab
from evals.judge import perdoc
from evals.report import build_summary, render_report
from evals.tests.fakes import StubBatches, StubClient, stub_message, write_tiny_case

CONFIG = load_config()
JUDGE = CONFIG["models"]["judge"]


def reply(**scores):
    return json.dumps({c: {"evidence": ['"quote"'], "issues": [], "score": scores.get(c, 3)} for c in ab.CRITERIA})


# ---------------------------------------------------------------- rubric files and version


def test_eval_10_six_criteria_in_three_groups():
    assert len(ab.CRITERIA) == len(set(ab.CRITERIA)) == 6
    assert list(ab.GROUPS) == ["a", "b", "c"] and all(len(g) == 2 for g in ab.GROUPS.values())
    assert ab.GROUPS["a"] == ("c1_accuracy", "c2_coherence")
    for g, crit in ab.GROUPS.items():
        text = ab.load_prompt(f"absolute_{g}", ab.RUBRICS_DIR).render("criteria")
        for c in crit:
            assert f"({c}, score 1–5)" in text
            for anchor in ("5 —", "4 —", "3 —", "2 —", "1 —"):
                assert anchor in text.split(f"({c}, score 1–5)")[1]


def test_eval_12_absolute_version_hashes_every_rubric_file(monkeypatch):
    v = ab.absolute_version(CONFIG)
    assert v.startswith(CONFIG["judge"]["version"] + "-abs-")
    files = ab.rubric_files()
    changed = [files[0]] + [type(f)(f.name, f.version, "0" * 64, f.parts) for f in files[1:]]
    monkeypatch.setattr(ab, "rubric_files", lambda: changed)
    assert ab.absolute_version(CONFIG) != v
    bumped = copy.deepcopy(CONFIG)
    bumped["judge"]["version"] = "j2"
    assert ab.absolute_version(bumped).startswith("j2-abs-")


# ---------------------------------------------------------------- request layout


@pytest.fixture
def case(tmp_path):
    return write_tiny_case(tmp_path / "cases")


def test_eval_10_request_is_anonymised_and_cache_friendly(case):
    reqs = {g: ab.build_request(case, "# Tinyco Q3 Report\n\n## Executive Summary\n\nText.", g) for g in ab.GROUPS}
    systems = {s for s, _ in reqs.values()}
    assert len(systems) == 1  # one system prompt, so the cached prefix is shared across groups
    contexts = {m[0]["content"][0]["text"] for _, m in reqs.values()}
    documents = {m[0]["content"][1]["text"] for _, m in reqs.values()}
    asks = [m[0]["content"][2]["text"] for _, m in reqs.values()]
    assert len(contexts) == 1 and len(documents) == 1 and len(set(asks)) == 3
    _, messages = reqs["a"]
    assert [b.get("cache_control") for b in messages[0]["content"]] == [{"type": "ephemeral"}] * 2 + [None]
    ctx = messages[0]["content"][0]["text"]
    assert case.purpose in ctx and case.style in ctx and "Team notes: shipped 7 things." in ctx  # attachments for C1
    assert "max 80 words" in ctx and '"synergy"' in ctx  # constraints for C4/C6
    assert '"c1_accuracy"' in asks[0] and '"c3_repetition"' not in asks[0]


# ---------------------------------------------------------------- parsing


def test_eval_10_parse_scores_variants():
    out = ab.parse_scores("Scores:\n```json\n" + reply(c1_accuracy=5) + "\n```", ab.GROUPS["a"])
    assert out["c1_accuracy"]["score"] == 5 and out["c2_coherence"]["evidence"] == ['"quote"']
    loose = json.dumps({"c1_accuracy": {"evidence": [], "score": "4"},
                        "c2_coherence": {"evidence": ["x"], "contradictions": ["s2 vs s4"], "score": 2.0}})
    out = ab.parse_scores(loose, ab.GROUPS["a"])
    assert out["c1_accuracy"]["score"] == 4 and out["c2_coherence"] == {"score": 2, "evidence": ["x"], "issues": ["s2 vs s4"]}


@pytest.mark.parametrize("bad", [
    "no json at all",
    '{"c1_accuracy": {"evidence": [], "score": 3}}',  # c2 missing
    '{"c1_accuracy": {"evidence": [], "score": 6}, "c2_coherence": {"evidence": [], "score": 3}}',
    '{"c1_accuracy": {"evidence": [], "score": 0}, "c2_coherence": {"evidence": [], "score": 3}}',
    '{"c1_accuracy": {"evidence": [], "score": 3.5}, "c2_coherence": {"evidence": [], "score": 3}}',
    '{"c1_accuracy": {"evidence": [], "score": "high"}, "c2_coherence": {"evidence": [], "score": 3}}',
    '{"c1_accuracy": {"evidence": [], "score": true}, "c2_coherence": {"evidence": [], "score": 3}}',
    '{"c1_accuracy": {"score": 3}, "c2_coherence": {"evidence": [], "score": 3}}',  # no evidence
    '{"c1_accuracy": 3, "c2_coherence": 3}',
    '[1, 2]',
])
def test_eval_10_parse_scores_rejects_malformed(bad):
    with pytest.raises(ValueError):
        ab.parse_scores(bad, ab.GROUPS["a"])


# ---------------------------------------------------------------- score rows (floors)


def _row(g, case_id, rep, group, version="v", status="ok", **scores):
    return {"custom_id": f"{g}-{case_id}-{rep}-{group}", "generator": g, "case_id": case_id, "rep": rep, "group": group,
            "version": version, "status": status,
            "scores": {c: {"score": scores.get(c, 4), "evidence": [], "issues": []} for c in ab.GROUPS[group]}}


def test_eval_10_score_rows_floor_and_c1():
    rows = [_row("g", "c1", 1, grp) for grp in ab.GROUPS]  # all 4s: passes the floor
    rows += [_row("g", "c2", 1, "a", c1_accuracy=1), _row("g", "c2", 1, "b"), _row("g", "c2", 1, "c")]
    rows += [_row("g", "c3", 1, "b", c4_formatting=2)]  # incomplete, but already a floor miss
    rows += [_row("g", "c4", 1, "c")]  # incomplete, no low score: floor unknown, not emitted
    rows += [_row("g", "c5", 1, "a", version="old", c1_accuracy=1)]  # another judge version: ignored
    by = {(r["case_id"], r["metric"]): r for r in ab.score_rows(rows, "v")}
    assert by[("c1", "abs_floor")]["value"] == 0.0 and by[("c1", "abs_c1_is_1")]["value"] == 0.0
    assert by[("c2", "abs_floor")]["value"] == 1.0 and by[("c2", "abs_c1_is_1")]["value"] == 1.0
    assert by[("c2", "abs_floor")]["detail"]["low"] == ["c1_accuracy"]
    assert by[("c3", "abs_floor")]["value"] == 1.0 and ("c3", "abs_c1_is_1") not in by
    assert ("c4", "abs_floor") not in by and by[("c4", "abs.c5_tone")]["value"] == 4.0
    assert not any(k[0] == "c5" for k in by)
    assert sum(1 for k in by if k[0] == "c1" and k[1].startswith("abs.")) == 6
    # A score of 3 passes floor 2 but not floor 3.
    three = [_row("g", "c9", 1, grp, c3_repetition=3) for grp in ab.GROUPS]
    assert [r["value"] for r in ab.score_rows(three, "v", floor=2) if r["metric"] == "abs_floor"] == [0.0]
    assert [r["value"] for r in ab.score_rows(three, "v", floor=3) if r["metric"] == "abs_floor"] == [1.0]


# ---------------------------------------------------------------- running


@pytest.fixture
def run_with_docs(tmp_path, case):
    store = RunStore("a1", tmp_path / "runs")
    for name in ("b0-oneshot", "b1-parallel"):
        store.save_result(asyncio.run(GENERATORS[name].generate(case, FakeLLM("claude-sonnet-5-5"), 1)))
    return case, store


def test_eval_10_fake_judge_end_to_end_and_resume(run_with_docs):
    case, store = run_with_docs
    v = ab.absolute_version(CONFIG)
    jobs, skipped = ab.plan_jobs(store.iter_results(), {case.id: case}, set())
    assert len(jobs) == 2 * 3 and skipped == 0
    asyncio.run(perdoc.run_with_llm(jobs, ab.make_fake_judge(CONFIG), store, ab.STREAM, v, role=ab.ROLE))
    rows = store.read("absolute")
    assert len(rows) == 6 and all(r["status"] == "ok" for r in rows)
    assert {r["group"] for r in rows} == set(ab.GROUPS)
    assert all(c["phase"] == "absolute" and c["requested_model"] == JUDGE and c["effort"] == CONFIG["effort"]["judge"]
               for c in store.read("calls"))
    metrics = {(r["generator"], r["metric"]) for r in ab.score_rows(rows, v)}
    assert ("b0-oneshot", "abs_floor") in metrics and ("b1-parallel", "abs.c6_instructions") in metrics
    again, _ = ab.plan_jobs(store.iter_results(), {case.id: case}, perdoc.done_ids(rows, v))
    assert again == []


def test_eval_10_errored_docs_are_skipped(run_with_docs):
    case, store = run_with_docs
    broken = store.load_result("b1-parallel", case.id, 1)
    broken.error = "infra: x"
    store.save_result(broken)
    jobs, skipped = ab.plan_jobs(store.iter_results(), {case.id: case}, set())
    assert len(jobs) == 3 and skipped == 1


def test_eval_21_absolute_refusal_and_malformed_are_error_rows_then_retried(run_with_docs):
    case, store = run_with_docs
    v = ab.absolute_version(CONFIG)
    jobs, _ = ab.plan_jobs(store.iter_results(), {case.id: case}, set())
    refused, garbled = jobs[0].custom_id, jobs[1].custom_id

    def responder(role, node_id, system, messages):
        if node_id == refused:
            return FakeRefusal("bio")
        if node_id == garbled:
            return '{"c1_accuracy": {"evidence": [], "score": 9}}'
        return ab.fake_absolute_responder(role, node_id, system, messages)

    asyncio.run(perdoc.run_with_llm(jobs, lambda: FakeLLM(JUDGE, "medium", 100, CONFIG, responder=responder),
                                    store, ab.STREAM, v, role=ab.ROLE))
    rows = {r["custom_id"]: r for r in store.read("absolute")}
    assert rows[refused]["status"] == "refusal" and rows[garbled]["status"] == "parse_error"
    assert sum(1 for r in rows.values() if r["status"] == "ok") == 4
    refusal_calls = [c for c in store.read("calls") if c["status"] == "refusal"]
    assert len(refusal_calls) == 2 and refusal_calls[0]["refusal_category"] == "bio"  # retried once
    # The refused doc's group is missing, so the doc is not scored on that group (never as 0).
    scored = ab.score_rows(store.read("absolute"), v)
    assert all(r["value"] >= 1 for r in scored if r["metric"].startswith("abs."))
    assert perdoc.unresolved_errors(store.read("absolute"), v) == 2
    again, _ = ab.plan_jobs(store.iter_results(), {case.id: case}, perdoc.done_ids(store.read("absolute"), v))
    assert sorted(j.custom_id for j in again) == sorted([refused, garbled])
    asyncio.run(perdoc.run_with_llm(again, ab.make_fake_judge(CONFIG), store, ab.STREAM, v, role=ab.ROLE))
    assert perdoc.unresolved_errors(store.read("absolute"), v) == 0


def test_eval_21_absolute_batches_by_custom_id_and_collects_earlier_batch(run_with_docs):
    case, store = run_with_docs
    v = ab.absolute_version(CONFIG)
    jobs, _ = ab.plan_jobs(store.iter_results(), {case.id: case}, set())

    def responder(cid, params):
        assert params["output_config"] == {"effort": CONFIG["effort"]["judge"]}
        assert not {"temperature", "top_p", "top_k", "fallbacks", "thinking"} & set(params)
        group = cid.rsplit("-", 1)[1]
        return stub_message(reply(c1_accuracy=5 if group == "a" else 3), model=JUDGE)

    batches = StubBatches(responder, polls_before_end=0)
    client = StubClient(batches=batches)
    # An interrupted invocation had already submitted a batch for every job.
    params = {j.custom_id: build_params(model=JUDGE, effort=CONFIG["effort"]["judge"], max_tokens=100,
                                        system=j.system, messages=j.messages) for j in jobs}
    asyncio.run(batches.create(requests=[{"custom_id": cid, "params": p} for cid, p in params.items()]))
    store.append("absolute_batches", [{"batch_id": "msgbatch_1", "event": "submitted", "custom_ids": [j.custom_id for j in jobs]}])

    async def sleep(s):
        return None

    asyncio.run(perdoc.run_with_batches(jobs, client, CONFIG, store, ab.STREAM, v, model=JUDGE,
                                        effort=CONFIG["effort"]["judge"], max_tokens=100, role=ab.ROLE, sleep=sleep))
    assert len(batches.created) == 1  # collected, not paid for twice
    rows = store.read("absolute")
    assert len(rows) == 6 and all(r["status"] == "ok" for r in rows)
    for r in rows:  # mapped by custom_id although results came back reversed
        first = ab.GROUPS[r["group"]][0]
        assert set(r["scores"]) == set(ab.GROUPS[r["group"]])
        assert r["scores"][first]["score"] == (5 if first == "c1_accuracy" else 3)


# ---------------------------------------------------------------- report and compare


def _judged_store(tmp_path, run_id, c1_scores, version=None):
    store = RunStore(run_id, tmp_path / "runs")
    v = version or ab.absolute_version(CONFIG)
    store.open_manifest({"mode": "fake", "generators": [{"name": "g1", "version": "1"}], "case_ids": [], "case_paths": [],
                         "reps": 1, "judge_version": "jv", "absolute_version": v, "models": CONFIG["models"],
                         "effort": CONFIG["effort"]})
    rows = []
    for i, s in enumerate(c1_scores):
        rows += [_row("g1", f"c{i}", 1, grp, version=v, c1_accuracy=s) for grp in ab.GROUPS]
    store.append("absolute", rows)
    return store


def test_eval_10_report_shows_floors_and_criteria(tmp_path):
    store = _judged_store(tmp_path, "r1", [1, 4, 2, 4])
    s = build_summary(store, CONFIG)
    assert s["metrics"]["abs_floor"]["g1"]["value"] == pytest.approx(0.5)
    assert s["metrics"]["abs_c1_is_1"]["g1"]["value"] == pytest.approx(0.25)
    assert s["absolute"]["abs.c1_accuracy"]["g1"]["value"] == pytest.approx(11 / 4)
    assert "abs.c1_accuracy" not in s["metrics"] and s["absolute_info"]["rows"] == 12
    text = render_report(s)
    assert "## Absolute rubric" in text and "never ranking" in text
    assert "| **Docs ≤ floor on any criterion** | 50.0% (n=4) |" in text
    assert "| **Docs with C1 accuracy = 1** | 25.0% (n=4) |" in text
    assert "| C1 accuracy vs data (mean) | 2.750 (n=4) |" in text


def test_eval_10_compare_flags_floors_never_means_and_checks_version(tmp_path):
    a = _judged_store(tmp_path, "a", [4] * 8)
    b = _judged_store(tmp_path, "b", [2] * 8)
    result = compare_runs(a, b, CONFIG, resamples=300)
    by = {r["metric"]: r for r in result["metrics"]}
    assert by["abs_floor"]["regression"] and by["abs_floor"]["delta"] == pytest.approx(1.0)
    assert by["abs.c1_accuracy"]["delta"] == pytest.approx(-2.0) and not by["abs.c1_accuracy"]["regression"]
    other = _judged_store(tmp_path, "c", [2] * 8, version="j9-abs-deadbeef")
    result = compare_runs(a, other, CONFIG, resamples=300)
    assert not any(r["metric"].startswith("abs") for r in result["metrics"])
    assert any("absolute_version differs" in w for w in result["warnings"])


# ---------------------------------------------------------------- runner, projection and CLI


def test_eval_20_projection_includes_absolute_and_claims(case):
    gens = [GENERATORS[n] for n in ("b0-oneshot", "b1-parallel")]
    proj = runner.project([case], gens, 2, CONFIG)
    lines = {ln.label: ln for ln in proj.lines}
    assert lines["judge absolute rubric"].calls == 3 * 2 * 2 and lines["judge claims check"].calls == 2 * 2
    assert lines["judge absolute rubric"].note == "(batches, 50%)"
    assert proj.generation_expected == pytest.approx(sum(lines[g.name].usd_expected for g in gens))
    only = runner.judge_only(proj, ["absolute"])
    assert [ln.label for ln in only.lines] == ["judge absolute rubric"]
    off = copy.deepcopy(CONFIG)
    off["absolute"]["enabled"] = False
    off["claims"]["enabled"] = False
    assert not any(ln.label in ("judge absolute rubric", "judge claims check")
                   for ln in runner.project([case], gens, 2, off).lines)


def test_runner_absolute_and_claims_runs_fake(tmp_path, case):
    gens = [GENERATORS["b0-oneshot"], GENERATORS["b1-parallel"]]
    store = RunStore("rr", tmp_path / "runs")
    store.open_manifest(runner.build_manifest([case], gens, 1, CONFIG, mode="fake", case_set="tmp"))
    asyncio.run(runner.run_generation(store, [case], gens, 1, CONFIG, log=lambda s: None))
    a = asyncio.run(runner.absolute_run(store, [case], CONFIG, log=lambda s: None))
    c = asyncio.run(runner.claims_run(store, [case], CONFIG, log=lambda s: None))
    assert a == {"calls": 6, "skipped": 0, "errors": 0} and c == {"calls": 2, "skipped": 0, "errors": 0}
    m = store.read_manifest()
    assert m["absolute_mode"] == "fake" and m["claims_mode"] == "fake" and m["absolute_batches"] is False
    assert m["absolute_version"] == ab.absolute_version(CONFIG)
    assert asyncio.run(runner.absolute_run(store, [case], CONFIG, log=lambda s: None))["calls"] == 0  # resume
    s = build_summary(store, CONFIG)
    assert s["cost"]["absolute"]["calls"] == 6 and s["cost"]["claims"]["calls"] == 2
    assert s["cost"]["judging_usd"] == pytest.approx(s["cost"]["absolute"]["usd"] + s["cost"]["claims"]["usd"])
    text = render_report(s)
    assert "| judge: absolute rubric | 6 |" in text and "| claims check | 2 |" in text and "## Claim checker" in text


def test_runner_absolute_live_path_with_stubbed_batches(tmp_path, case):
    gens = [GENERATORS["b0-oneshot"]]
    store = RunStore("live-abs", tmp_path / "runs")
    store.open_manifest(runner.build_manifest([case], gens, 1, CONFIG, mode="live", case_set="tmp"))
    store.save_result(asyncio.run(gens[0].generate(case, FakeLLM("claude-sonnet-5-5"), 1)))
    client = StubClient(batches=StubBatches(lambda cid, p: stub_message(reply(), model=p["model"]), 0))
    asyncio.run(runner.absolute_run(store, [case], CONFIG, live=True, client=client, log=lambda s: None, poll_s=0))
    asyncio.run(runner.claims_run(store, [case], CONFIG, live=True, client=client, log=lambda s: None, poll_s=0))
    assert all(r["status"] == "ok" and r.get("batch") for r in store.read("absolute"))
    (claims_row,) = store.read("claims")
    assert claims_row["status"] == "parse_error"  # the stub replied with rubric JSON: an error row, not a score
    m = store.read_manifest()
    assert m["absolute_mode"] == "live" and m["absolute_batches"] is True


def test_cli_judge_only_flag_and_config_switches():
    from evals.__main__ import judge_kinds, parser

    args = parser().parse_args(["judge", "--only", "absolute", "claims"])
    assert judge_kinds(args, CONFIG) == ["absolute", "claims"]
    assert judge_kinds(parser().parse_args(["judge"]), CONFIG) == ["pairwise", "absolute", "claims"]
    off = copy.deepcopy(CONFIG)
    off["claims"]["enabled"] = False
    assert judge_kinds(parser().parse_args(["judge"]), off) == ["pairwise", "absolute"]
    with pytest.raises(SystemExit):
        parser().parse_args(["judge", "--only", "rubric"])
