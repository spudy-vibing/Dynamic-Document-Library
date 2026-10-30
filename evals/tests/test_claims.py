"""EVAL-11: claim checker — deterministic verification, rule extractor, model path, error rows, resume."""

from __future__ import annotations

import asyncio
import json

import pytest

from evals.core.cases import load_case
from evals.core.config import load_config
from evals.core.llm import FakeLLM, FakeRefusal
from evals.core.storage import RunStore
from evals.judge import claims as cl
from evals.judge import perdoc
from evals.perturb.samples import build_result
from evals.tests.fakes import StubBatches, StubClient, stub_message, write_tiny_case

CONFIG = load_config()
TOL = float(CONFIG["grading"]["number_tolerance"])
CLAIMS_MODEL = CONFIG["models"]["claims"]

TRUTH = {
    "values": {
        "revenue_prior": 3980000, "revenue_this": 4210000,
        "margin_prior_pct": 22.9, "margin_pct": 23.4, "opex_change_pct": 31.7,
        "churn_prior_pct": 4.6, "churn_pct": 4.5, "nps": None,
        "north_tickets_closed": 23, "south_tickets_closed": 57,
    },
    "labels": {
        "revenue_prior": ["Q2 revenue", "prior-quarter revenue", "revenue"],
        "revenue_this": ["Q3 revenue", "revenue"],
        "margin_prior_pct": ["prior-quarter gross margin", "gross margin", "margin"],
        "margin_pct": ["gross margin", "margin"],
        "opex_change_pct": ["operating expenses", "opex"],
        "churn_prior_pct": ["churn"], "churn_pct": ["churn"],
        "nps": ["NPS"],
        "north_tickets_closed": ["North tickets", "tickets"],
        "south_tickets_closed": ["South tickets", "tickets"],
    },
    "derived": {
        "revenue_change": {"value": 230000, "formula": "revenue_this - revenue_prior"},
        "revenue_growth_pct": {"value": 5.8, "formula": "(revenue_this / revenue_prior - 1) * 100"},
        "margin_change_pp": {"value": 0.5, "formula": "margin_pct - margin_prior_pct"},
        "churn_change_pp": {"value": -0.1, "formula": "churn_pct - churn_prior_pct"},
        "tickets_total": {"value": 80, "formula": "north_tickets_closed + south_tickets_closed"},
    },
    "facts": {"company": "Tinyco", "revenue_direction": "up", "north_lead": "Ama Brennan"},
}


@pytest.fixture
def case(tmp_path):
    c = write_tiny_case(tmp_path / "cases")
    (c.path / "truth.json").write_text(json.dumps(TRUTH))
    return load_case(c.path)


@pytest.fixture
def truth(case):
    return cl.Truth(case, TOL)


def doc(case, summary="", analysis="", nxt="- a\n- b\n- c", generator="b0-oneshot"):
    return build_result(case, {"summary": summary, "analysis": analysis, "next": nxt}, generator=generator)


# ---------------------------------------------------------------- ground truth helpers


def test_eval_11_period_family_counterparts_and_directions(truth):
    assert cl.period_family("revenue_prior") == cl.period_family("revenue_this") == "revenue"
    assert cl.period_family("margin_prior_pct") == "margin_pct"
    assert truth.counterparts("revenue_this") == {"revenue_prior"}
    assert truth.counterparts("north_tickets_closed") == set()
    assert truth.direction("revenue_this") == "up"  # facts.revenue_direction
    assert truth.direction("margin_pct") == "up"  # sign of margin_change_pp
    assert truth.direction("churn_pct") == "down"
    assert truth.direction("churn_change_pp") == "down"
    assert truth.direction("north_tickets_closed") is None  # a sum across teams is not a period change


# ---------------------------------------------------------------- deterministic verification


@pytest.mark.parametrize(
    "claim, verdict",
    [
        ({"metric": "revenue_this", "value": 4210000, "direction": "up"}, "correct"),
        ({"metric": "revenue_this", "value": None, "text": "Revenue reached $4.21M."}, "correct"),
        ({"metric": "margin_pct", "value": 31.7}, "misattributed"),  # T2: the opex figure
        ({"metric": "revenue_this", "value": 3980000}, "wrong_period"),  # T6: the prior quarter's
        ({"metric": "south_tickets_closed", "value": 23}, "misattributed"),  # team swap
        ({"metric": "churn_pct", "value": 4.5, "direction": "up"}, "wrong_direction"),
        ({"metric": "revenue_this", "value": None, "direction": "down", "text": "Revenue fell."}, "wrong_direction"),
        ({"metric": "revenue_this", "value": None, "direction": "up", "text": "Revenue grew."}, "unquantified"),
        ({"metric": "margin_pct", "value": 0.5, "direction": "up"}, "correct"),  # its derived change
        ({"metric": "margin_change_pp", "value": 0.5}, "correct"),
        ({"metric": "revenue_growth_pct", "value": 5.8}, "correct"),
        ({"metric": "margin_pct", "value": 0.234}, "correct"),  # a fraction for a _pct key
        ({"metric": "revenue_this", "value": 9990000}, "wrong_value"),
        ({"metric": "nps", "value": 45}, "wrong_value"),  # T4: a figure for a missing KPI
    ],
)
def test_eval_11_verify(truth, claim, verdict):
    claim = {"text": "", "direction": None, **claim}
    assert cl.verify(truth, claim)[0] == verdict


def test_eval_11_verify_leaves_facts_and_unmapped_claims_to_the_model(truth):
    assert cl.verify(truth, {"metric": "north_lead", "value": None, "text": "Ama leads North."})[0] is None
    assert cl.verify(truth, {"metric": None, "value": 4210000, "text": "x"})[0] is None
    assert cl.verify(truth, {"metric": "made_up", "value": 1, "text": "x"})[0] is None


# ---------------------------------------------------------------- rule extractor (true and false positives)


def test_eval_11_rule_claims_true_positives(case):
    r = doc(
        case,
        summary="Revenue rose to $4.21M in the quarter. Gross margin was 31.7%. Q3 revenue was $3.98M.",
        analysis="Churn rose to 4.5%. South closed 23 tickets. NPS was 45.",
    )
    by_text = {c["text"]: c["verdict"] for c in cl.rule_claims(case, r, TOL)}
    assert by_text["Revenue rose to $4.21M in the quarter."] == "correct"
    assert by_text["Gross margin was 31.7%."] == "misattributed"
    assert by_text["Q3 revenue was $3.98M."] == "wrong_period"
    assert by_text["Churn rose to 4.5%."] == "wrong_direction"
    assert by_text["South closed 23 tickets."] == "misattributed"
    assert by_text["NPS was 45."] == "wrong_value"


def test_eval_11_rule_claims_no_false_positives_on_correct_text(case):
    r = doc(
        case,
        summary="Revenue grew 5.8% to $4.21M, up from $3.98M in Q2. Gross margin improved by 0.5 points to 23.4%.",
        analysis="Churn fell to 4.5% from 4.6%. North closed 23 tickets and South closed 57. Operating expenses rose 31.7%. "
                 "We shipped 12 features.",
    )
    found = cl.rule_claims(case, r, TOL)
    assert found, "correct claims should still be extracted"
    bad = [c for c in found if c["verdict"] in cl.MISATTRIB | {"wrong_value"}]
    assert bad == []
    assert not any("12 features" in c["text"] for c in found)  # no label, no claim


def test_eval_11_swapped_near_collision_figures_are_caught(case):
    """Perturbation (EVALS_PROPOSAL §6.4): swap two near-collision figures between their labels."""
    clean = "Gross margin was 23.4%. Operating expenses rose 31.7%."
    swapped = clean.replace("23.4", "@@").replace("31.7", "23.4").replace("@@", "31.7")
    v_clean = [c["verdict"] for c in cl.rule_claims(case, doc(case, summary=clean), TOL)]
    v_swap = [c["verdict"] for c in cl.rule_claims(case, doc(case, summary=swapped), TOL)]
    assert set(v_clean) == {"correct"}
    assert v_swap.count("misattributed") == 2


# ---------------------------------------------------------------- model path: parsing, verification, metrics


def test_eval_11_parse_claims_and_malformed_replies():
    reply = "```json\n" + json.dumps({"claims": [
        {"section": "summary", "text": "Revenue was $4.21M.", "metric": "revenue_this", "value": "4,210,000",
         "direction": "UP", "verdict": "Correct", "reason": "matches"},
        {"section": "summary", "text": "Odd.", "verdict": "nonsense"},
    ]}) + "\n```"
    out = cl.parse_claims(reply)
    assert out[0]["value"] == 4210000.0 and out[0]["direction"] == "up" and out[0]["verdict"] == "correct"
    assert out[1]["verdict"] == "unverifiable" and out[1]["metric"] is None
    assert cl.parse_claims('{"claims": []}') == []
    for bad in ("no json", '{"claims": "x"}', '{"other": []}', '{"claims": [{"verdict": "correct"}]}'):
        with pytest.raises(ValueError):
            cl.parse_claims(bad)


def test_eval_11_rule_overrides_model_verdict_and_records_agreement(case):
    model_claims = [
        {"section": "summary", "text": "Gross margin was 31.7%.", "metric": "margin_pct", "value": 31.7,
         "direction": None, "verdict": "correct"},  # the model missed it; the rule does not
        {"section": "summary", "text": "Bo leads North.", "metric": "north_lead", "value": None,
         "direction": None, "verdict": "misattributed"},  # a fact: the model's verdict stands
        {"section": "summary", "text": "Revenue was $4.21M.", "metric": "revenue_this", "value": 4210000,
         "direction": None, "verdict": "correct"},
    ]
    out = cl.check_claims(case, model_claims, TOL)
    assert [(c["verdict"], c["checked_by"], c["model_verdict"]) for c in out] == [
        ("misattributed", "rule", "correct"), ("misattributed", "model", "misattributed"), ("correct", "rule", "correct"),
    ]


def test_eval_11_silent_vs_flagged(case):
    r = doc(case, summary="Gross margin was 31.7%. Revenue was $4.21M.")
    found = cl.check_claims(case, cl.rule_claims(case, r, TOL), TOL)
    m = cl.doc_metrics(r, found, TOL)
    assert (m["misattrib"]["num"], m["misattrib"]["den"]) == (1, 2)
    assert m["misattrib_silent"]["num"] == 1 and m["docs_misattrib_silent"]["value"] == 1.0
    r.flagged_numbers = [("summary", "31.7%")]  # the generator flagged it: not silent
    m = cl.doc_metrics(r, found, TOL)
    assert m["misattrib"]["num"] == 1 and m["misattrib_silent"]["num"] == 0 and m["docs_misattrib_silent"]["value"] == 0.0


def test_eval_11_request_has_prose_only_truth_and_no_generator_identity(case):
    r = doc(case, summary="Revenue was $4.21M.", generator="b1-parallel")
    system, messages = cl.build_request(case, r)
    blob = system + json.dumps(messages)
    assert "b1-parallel" not in blob and "parallel" not in blob
    document = messages[0]["content"][1]["text"]
    assert '<section id="summary"' in document and "Revenue was $4.21M." in document
    assert "| Metric | Value |" not in document  # data blocks are not sent as prose
    assert "revenue_direction" in blob and messages[0]["content"][0]["cache_control"] == {"type": "ephemeral"}


# ---------------------------------------------------------------- running: fake, refusals, parse errors, resume, batches


def _store(tmp_path, case, texts):
    store = RunStore("c1", tmp_path / "runs")
    for i, (g, summary) in enumerate(texts):
        store.save_result(doc(case, summary=summary, generator=g))
    return store


def test_eval_11_fake_checker_end_to_end_and_resume(tmp_path, case):
    store = _store(tmp_path, case, [("b0-oneshot", "Gross margin was 31.7%."), ("b1-parallel", "Revenue was $4.21M.")])
    v = cl.claims_version(CONFIG)
    jobs, skipped = cl.plan_jobs(store.iter_results(), {case.id: case}, set(), CONFIG)
    assert len(jobs) == 2 and skipped == 0
    asyncio.run(perdoc.run_with_llm(jobs, cl.make_fake_checker(CONFIG, jobs), store, cl.STREAM, v, role=cl.ROLE))
    rows = store.read("claims")
    assert len(rows) == 2 and all(r["status"] == "ok" and r["version"] == v for r in rows)
    calls = store.read("calls")
    assert all(c["phase"] == "claims" and c["requested_model"] == CLAIMS_MODEL and c["effort"] == CONFIG["effort"]["claims"]
               for c in calls)
    scores = {(s["generator"], s["metric"]): s for s in cl.score_rows(rows, v)}
    assert scores[("b0-oneshot", "misattrib")]["num"] == 1 and scores[("b1-parallel", "misattrib")]["num"] == 0
    assert cl.score_rows(rows, "other-version") == []
    again, _ = cl.plan_jobs(store.iter_results(), {case.id: case}, perdoc.done_ids(rows, v), CONFIG)
    assert again == []


def test_eval_21_claims_refusal_and_malformed_reply_are_error_rows_then_retried(tmp_path, case):
    store = _store(tmp_path, case, [("b0-oneshot", "Gross margin was 31.7%."), ("b1-parallel", "Revenue was $4.21M.")])
    v = cl.claims_version(CONFIG)
    jobs, _ = cl.plan_jobs(store.iter_results(), {case.id: case}, set(), CONFIG)
    refused, malformed = jobs[0].custom_id, jobs[1].custom_id

    def responder(role, node_id, system, messages):
        return FakeRefusal("cyber") if node_id == refused else "I could not find any claims."

    make = lambda: FakeLLM(CLAIMS_MODEL, "medium", 100, CONFIG, responder=responder)  # noqa: E731
    asyncio.run(perdoc.run_with_llm(jobs, make, store, cl.STREAM, v, role=cl.ROLE))
    rows = {r["custom_id"]: r for r in store.read("claims")}
    assert rows[refused]["status"] == "refusal" and rows[malformed]["status"] == "parse_error"
    assert cl.score_rows(store.read("claims"), v) == []  # never scored
    kinds = sorted(e["kind"] for e in store.read("errors"))
    assert kinds == ["parse_error", "refusal", "refusal", "refusal"]  # 2 refused calls + the refusal row
    assert perdoc.unresolved_errors(store.read("claims"), v) == 2
    # Resume retries both and the latest ok row wins.
    again, _ = cl.plan_jobs(store.iter_results(), {case.id: case}, perdoc.done_ids(store.read("claims"), v), CONFIG)
    assert len(again) == 2
    asyncio.run(perdoc.run_with_llm(again, cl.make_fake_checker(CONFIG, again), store, cl.STREAM, v, role=cl.ROLE))
    assert perdoc.unresolved_errors(store.read("claims"), v) == 0
    assert len(cl.score_rows(store.read("claims"), v)) == 2 * 3


def test_eval_21_claims_batches(tmp_path, case):
    store = _store(tmp_path, case, [("b0-oneshot", "Gross margin was 31.7%."), ("b1-parallel", "Revenue was $4.21M.")])
    v = cl.claims_version(CONFIG)
    jobs, _ = cl.plan_jobs(store.iter_results(), {case.id: case}, set(), CONFIG)
    by_id = {j.custom_id: j for j in jobs}
    seen: dict[str, int] = {}

    def responder(cid, params):
        seen[cid] = seen.get(cid, 0) + 1
        assert params["model"] == CLAIMS_MODEL and params["output_config"] == {"effort": CONFIG["effort"]["claims"]}
        assert not {"temperature", "top_p", "top_k", "fallbacks", "thinking"} & set(params)
        if cid == jobs[0].custom_id and seen[cid] == 1:
            return stub_message("", model=CLAIMS_MODEL, stop_reason="refusal", category="cyber")
        j = by_id[cid]
        return stub_message(json.dumps({"claims": cl.rule_claims(j.case, j.result, TOL)}), model=CLAIMS_MODEL,
                            input_tokens=1000, output_tokens=100)

    async def sleep(s):
        return None

    batches = StubBatches(responder, polls_before_end=1)
    asyncio.run(perdoc.run_with_batches(jobs, StubClient(batches=batches), CONFIG, store, cl.STREAM, v,
                                        model=CLAIMS_MODEL, effort=CONFIG["effort"]["claims"], max_tokens=100,
                                        role=cl.ROLE, sleep=sleep))
    assert len(batches.created) == 2 and [r["custom_id"] for r in batches.created[1]] == [jobs[0].custom_id]
    rows = store.read("claims")
    assert len(rows) == 2 and all(r["status"] == "ok" and r["batch"] for r in rows)
    ok = [c for c in store.read("calls") if c["status"] == "ok"]
    p = CONFIG["prices"][CLAIMS_MODEL]
    assert all(c["cost_usd"] == pytest.approx(0.5 * (1000 * p["input"] + 100 * p["output"]) / 1e6) for c in ok)
    assert [r["event"] for r in store.read("claims_batches")] == ["submitted", "collected", "submitted", "collected"]


def test_eval_21_claims_batches_served_model_mismatch_is_infra(tmp_path, case):
    store = _store(tmp_path, case, [("b0-oneshot", "Revenue was $4.21M.")])
    jobs, _ = cl.plan_jobs(store.iter_results(), {case.id: case}, set(), CONFIG)

    async def sleep(s):
        return None

    batches = StubBatches(lambda cid, p: stub_message('{"claims": []}', model="claude-other"), 0)
    asyncio.run(perdoc.run_with_batches(jobs, StubClient(batches=batches), CONFIG, store, cl.STREAM, "v",
                                        model=CLAIMS_MODEL, effort="medium", max_tokens=100, role=cl.ROLE, sleep=sleep))
    (r,) = store.read("claims")
    assert r["status"] == "infra" and cl.score_rows([r], "v") == []
