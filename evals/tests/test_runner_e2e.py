"""End-to-end FAKE runs: run -> grade -> judge -> report, resume, refusals, budget (EVAL-15, 18, 20)."""

from __future__ import annotations

import asyncio
import copy
import json

import pytest

from evals import runner
from evals.__main__ import main
from evals.core.config import load_config
from evals.core.llm import FakeLLM, FakeRefusal
from evals.core.storage import RunStore
from evals.generators import GENERATORS
from evals.tests.fakes import WordCountGrader, write_tiny_case

CONFIG = load_config()
GENS = [GENERATORS[n] for n in ("b0-oneshot", "b1-parallel", "b2-chain")]


@pytest.fixture
def setup(tmp_path):
    cases = [write_tiny_case(tmp_path / "cases", case_id=f"tiny-0{i}") for i in (1, 2)]
    store = RunStore("e2e", tmp_path / "runs")
    store.open_manifest(runner.build_manifest(cases, GENS, 2, CONFIG, mode="fake", case_set="tmp"))
    return cases, store, tmp_path


def test_eval_15_fake_pipeline_writes_every_file(setup):
    cases, store, tmp = setup
    counts = asyncio.run(runner.run_generation(store, cases, GENS, 2, CONFIG, log=lambda s: None))
    assert counts == {"slots": 12, "ok": 12, "errors": 0, "skipped_done": 0}
    for g in GENS:
        for c in cases:
            for rep in (1, 2):
                base = store.doc_base(g.name, c.id, rep)
                assert base.with_suffix(".md").read_text().startswith("# Tinyco")
                assert json.loads(base.with_suffix(".json").read_text())["generator"] == g.name

    m = store.read_manifest()
    assert m["mode"] == "fake" and m["case_ids"] == ["tiny-01", "tiny-02"] and m["reps"] == 2
    assert {g["name"] for g in m["generators"]} == {g.name for g in GENS}
    assert m["judge_version"] and m["case_set_hash"] and "git_sha" in m
    assert m["models"] == CONFIG["models"] and m["effort"] == CONFIG["effort"]

    calls = store.read("calls")
    assert len(calls) == 2 * 2 * (1 + 3 + 3) and all(c["phase"] == "generate" for c in calls)

    n = runner.grade_run(store, runner.cases_from_manifest(m), [WordCountGrader()], log=lambda s: None)
    scores = store.read("scores")
    assert n == len(scores) and {s["metric"] for s in scores} >= {"length_ratio", "structure", "cost_usd", "wall_ms", "cache_hit"}
    assert all(s["grader"] in ("test_words", "harness") for s in scores)

    j = asyncio.run(runner.judge_run(store, cases, CONFIG, log=lambda s: None))
    assert j == {"pairs": 8, "orders": 16, "skipped": 0}  # 2 pairs x 2 cases x 2 reps, both orders
    assert len(store.read("judge")) == 16

    from evals.report import write_report

    report, summary = write_report(store, CONFIG, scoreboard_dir=tmp / "sb")
    text = report.read_text()
    assert "FAKE MODE — not a real result" in text
    assert "### b0-oneshot vs b1-parallel" in text and "### b2-chain vs b1-parallel" in text
    assert "| t4_missing |" in text and "judging $" in text
    data = json.loads(summary.read_text())
    assert data["cost"]["judge"]["calls"] == 16 and data["cost"]["generation_usd"] > 0

    # Resume: nothing left to do, and judging again adds no rows.
    again = asyncio.run(runner.run_generation(store, cases, GENS, 2, CONFIG, log=lambda s: None))
    assert again["slots"] == 0 and again["skipped_done"] == 12
    asyncio.run(runner.judge_run(store, cases, CONFIG, log=lambda s: None))
    assert len(store.read("judge")) == 16


def test_refusals_and_infra_errors_are_error_rows_never_scored(setup):
    cases, store, _ = setup
    case = cases[0]
    gens = [GENERATORS["b0-oneshot"]]

    def make_llm():
        return FakeLLM(CONFIG["models"]["generator"], "medium", 100, CONFIG, script=[FakeRefusal("cyber"), FakeRefusal("cyber")])

    counts = asyncio.run(runner.run_generation(store, [case], gens, 1, CONFIG, make_llm=make_llm, log=lambda s: None))
    assert counts["errors"] == 1
    result = store.load_result("b0-oneshot", case.id, 1)
    assert result.error.startswith("refusal") and len(result.calls) == 2
    kinds = [e["kind"] for e in store.read("errors")]
    assert kinds == ["refusal", "refusal", "slot_refused"]
    runner.grade_run(store, [case], [WordCountGrader()], log=lambda s: None)
    assert store.read("scores") == []
    # The errored slot is retried on resume.
    counts = asyncio.run(runner.run_generation(store, [case], gens, 1, CONFIG, log=lambda s: None))
    assert counts["ok"] == 1 and store.load_result("b0-oneshot", case.id, 1).error is None


def test_generator_bug_is_recorded_not_raised(setup):
    cases, store, _ = setup

    class Broken:
        name, version = "broken", "0"

        async def generate(self, case, llm, rep):
            raise ZeroDivisionError("bug")

    counts = asyncio.run(runner.run_generation(store, cases[:1], [Broken()], 1, CONFIG, log=lambda s: None))
    assert counts["errors"] == 1 and store.read("errors")[0]["kind"] == "exception"


def test_eval_20_projection_and_budget(setup):
    cases, _, _ = setup
    proj = runner.project(cases, GENS, 3, CONFIG)
    labels = [ln.label for ln in proj.lines]
    assert labels[:3] == ["b0-oneshot", "b1-parallel", "b2-chain"] and any(lb.startswith("judge") for lb in labels)
    assert 0 < proj.expected < proj.worst
    b0, b1, b2 = proj.lines[:3]
    assert (b0.calls, b1.calls, b2.calls) == (2 * 3, 2 * 3 * 3, 2 * 3 * 3)
    text = proj.format()
    assert "TOTAL" in text and "Cap run.max_usd" in text
    runner.check_budget(proj)
    tight = copy.deepcopy(CONFIG)
    tight["run"]["max_usd"] = 0.0001
    with pytest.raises(runner.BudgetExceeded):
        runner.check_budget(runner.project(cases, GENS, 3, tight))


def test_eval_18_live_requires_key(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(runner.MissingKey):
        runner.require_key()
    assert main(["run", "--live", "--reps", "1", "--runs-dir", str(tmp_path)]) == 2
    assert not list(tmp_path.iterdir())  # nothing started


def _real_cases_or_skip():
    try:
        from evals.graders import ALL_GRADERS  # noqa: F401

        cases = runner.select_cases("dev")
    except (ImportError, SystemExit) as e:
        pytest.skip(f"real graders or dev cases not available: {e}")
    return cases


def test_cli_all_on_real_cases_and_graders(tmp_path, capsys):
    cases = _real_cases_or_skip()
    args = ["--reps", "1", "--cases", cases[0].id, "--runs-dir", str(tmp_path / "runs"),
            "--scoreboard-dir", str(tmp_path / "sb")]
    assert main(["all", "--run-id", "cli-a", *args]) == 0
    assert main(["all", "--run-id", "cli-b", *args]) == 0
    run = tmp_path / "runs" / "cli-a"
    for name in ("manifest.json", "calls.jsonl", "scores.jsonl", "judge.jsonl", "report.md"):
        assert (run / name).exists(), name
    assert (tmp_path / "sb" / "cli-a.summary.json").exists() and (tmp_path / "sb" / "cli-a.md").exists()
    metrics = {json.loads(line)["metric"] for line in (run / "scores.jsonl").read_text().splitlines()}
    assert "unr" in metrics and "structure" in metrics
    assert main(["compare", "cli-a", "cli-b", "--runs-dir", str(tmp_path / "runs"), "--fail-on-regression"]) == 0
    out = capsys.readouterr().out
    assert "Regressions: **0**" in out  # identical fake runs
    assert main(["project", "--reps", "1", "--cases", cases[0].id]) == 0


def test_live_code_path_with_stubbed_client(setup):
    """The live path (AnthropicLLM inside the runner, live Batches judge) with a stub client: no network."""
    from evals.tests.fakes import StubBatches, StubClient, stub_message

    cases, _, tmp = setup
    store = RunStore("live-stub", tmp / "runs")
    gens = [GENERATORS["b0-oneshot"], GENERATORS["b1-parallel"]]
    store.open_manifest(runner.build_manifest(cases[:1], gens, 1, CONFIG, mode="live", case_set="tmp"))
    model, judge = CONFIG["models"]["generator"], CONFIG["models"]["judge"]
    doc = "## Executive Summary\n\nRevenue was 4,210,000.\n\n## Financial Analysis\n\nMargin 23.4%.\n\n## Next Steps\n\n- a\n- b\n- c"
    outcomes = [stub_message(doc, model=model)] + [stub_message("Section text.", model=model) for _ in range(3)]
    verdict = json.dumps({q: {"evidence": [], "verdict": "A"} for q in ("q1_coherence", "q2_redundancy", "q3_purpose", "overall")})
    client = StubClient(outcomes, batches=StubBatches(lambda cid, p: stub_message(verdict, model=judge), 0))
    counts = asyncio.run(runner.run_generation(store, cases[:1], gens, 1, CONFIG, live=True, client=client, log=lambda s: None))
    assert counts["ok"] == 2
    assert all(r["output_config"] == {"effort": CONFIG["effort"]["generator"]} for r in client.messages.requests)
    b0 = store.load_result("b0-oneshot", cases[0].id, 1)
    assert [s.model_text() for s in b0.sections][0] == "Revenue was 4,210,000."
    asyncio.run(runner.judge_run(store, cases[:1], CONFIG, live=True, client=client, log=lambda s: None, poll_s=0))
    rows = store.read("judge")
    assert len(rows) == 2 and all(r["status"] == "ok" and r.get("batch") for r in rows)
    m = store.read_manifest()
    assert m["judge_mode"] == "live" and m["judge_batches"] is True
