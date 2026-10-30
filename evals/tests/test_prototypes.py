"""EVAL-4 / WP-017: coherence prototypes P1-P4 under FakeLLM (no network)."""

from __future__ import annotations

import asyncio
import json
import re

import pytest

from evals import runner
from evals.__main__ import main
from evals.core.config import load_config
from evals.core.storage import RunStore
from evals.core.llm import FakeLLM, FakeRefusal, FakeReply, ServedModelMismatch, default_fake_responder, message_text
from evals.core.types import RefusalError
from evals.generators import DEFAULT_GENERATORS, GENERATORS, PROTOTYPES
from evals.generators import coherence as co
from evals.generators import common as c
from evals.report import ablation, render_ablation
from evals.tests.fakes import write_tiny_case

MODEL = "claude-sonnet-5-5"
CONFIG = load_config()
PROTOS = ("p1-frame", "p2-brief", "p3-flow", "p4-editor")


@pytest.fixture
def case(tmp_path):
    return write_tiny_case(tmp_path)


def valid_brief(case) -> str:
    points = "\n".join(f"- {s.id}: the key point of {s.title}." for s in case.sections)
    return (f"## Core message\nRevenue grew.\n\n## Section points\n{points}\n\n"
            "## Terms\n- \"revenue\", not \"sales\"\n\n## Avoid\n- speculation")


_EDITABLE = re.compile(r'<draft_section id="([^"]+)"[^>]*>.*?<editable>\n(.*?)\n</editable>', re.S)


def smart_responder(case, edits_fn=None):
    """Valid brief for _brief, JSON edits for _editor (from edits_fn(draft)), else the default fake text."""

    def respond(role, node_id, system, messages):
        if role == "brief":
            return valid_brief(case)
        if role == "editor":
            draft = dict(_EDITABLE.findall(message_text(messages[0]["content"])))
            edits = edits_fn(draft) if edits_fn else []
            return json.dumps({"edits": edits, "notes": ["checked the draft"]})
        return default_fake_responder(role, node_id, system, messages)

    return respond


def gen(name, case, llm=None):
    llm = llm or FakeLLM(MODEL, responder=smart_responder(case))
    return asyncio.run(GENERATORS[name].generate(case, llm, 1)), llm


def blocks(req):
    return req["messages"][0]["content"]


def breakpoints(req):
    n = sum(1 for m in req["messages"] if isinstance(m["content"], list) for b in m["content"] if b.get("cache_control"))
    return n + sum(1 for b in req.get("system", []) if isinstance(b, dict) and b.get("cache_control"))


# ---------------------------------------------------------------- registry


def test_registry_versions_and_not_default():
    for name in PROTOS:
        g = GENERATORS[name]
        assert g.name == name and name in PROTOTYPES and name not in DEFAULT_GENERATORS
        assert "p1_frame@1-" in g.version and "_shared@1-" in g.version
    assert "p2_brief@" in GENERATORS["p2-brief"].version and "p3_flow@" in GENERATORS["p3-flow"].version
    assert all(t in GENERATORS["p4-editor"].version for t in ("p2_brief@", "p4_editor@"))
    for _, over in PROTOTYPES.values():
        assert over in GENERATORS


# ---------------------------------------------------------------- request layout (FR-PRV-7)


@pytest.mark.parametrize("name", PROTOS)
def test_request_layout_shared_prefix_first_and_at_most_two_breakpoints(case, name):
    result, llm = gen(name, case)
    assert result.error is None and [s.id for s in result.sections] == ["summary", "analysis", "next"]
    frame = co.frame_text(case)
    for req in llm.requests:
        assert req["system"] == c.system_prompt() and req["output_config"] == {"effort": "medium"}
        first = blocks(req)
        assert first[0]["text"] == frame and first[1]["text"] == c.context_text(case)
        assert breakpoints(req) <= co.MAX_BREAKPOINTS
        assert first[1].get("cache_control") or first[2].get("cache_control")  # breakpoint on the last static block
        assert "cache_control" not in first[-1]  # the per-prompt part comes last, uncached
    sections = [r for r, call in zip(llm.requests, llm.calls) if call.role == "generate"]
    for req, s in zip(sections, case.sections):
        per_prompt = blocks(req)[-1]["text"]
        assert per_prompt.startswith(co.position_line(case, s)) and f'section titled "{s.title}"' in per_prompt
        assert "Each section covers only its own scope" in blocks(req)[0]["text"]


@pytest.mark.parametrize("name", ["p1-frame", "p2-brief", "p4-editor"])
def test_parallel_prototypes_share_a_byte_identical_prefix(case, name):
    _, llm = gen(name, case)
    sections = [r for r, call in zip(llm.requests, llm.calls) if call.role == "generate"]
    prefixes = {json.dumps(blocks(r)[:-1], sort_keys=True) for r in sections}
    assert len(sections) == 3 and len(prefixes) == 1
    assert all(breakpoints(r) == 1 for r in sections)


def test_p1_keeps_b1_dependencies_in_the_per_prompt_part(case):
    _, llm = gen("p1-frame", case)
    by_node = {call.node_id: req for req, call in zip(llm.requests, llm.calls)}
    nxt = blocks(by_node["next"])[-1]["text"]
    assert '<written_section title="Executive Summary">' in nxt and '<written_section title="Financial Analysis">' in nxt
    assert "<written_section" not in blocks(by_node["summary"])[-1]["text"]
    assert "<shared_brief>" not in json.dumps(by_node["next"])


def test_p2_brief_runs_first_and_its_prefix_is_a_prefix_of_every_section(case):
    result, llm = gen("p2-brief", case)
    assert [call.role for call in llm.calls] == ["brief", "generate", "generate", "generate"]
    assert llm.calls[0].node_id == "_brief"
    brief_req = llm.requests[0]
    assert "You are writing section" not in blocks(brief_req)[-1]["text"]  # helper prompts get no position line
    assert len(blocks(brief_req)) == 3 and blocks(brief_req)[1].get("cache_control")
    for req in llm.requests[1:]:
        b = blocks(req)
        assert [x["text"] for x in b[:2]] == [x["text"] for x in blocks(brief_req)[:2]]
        assert b[2]["text"] == co.brief_prefix(valid_brief(case)) and b[2]["cache_control"] == {"type": "ephemeral"}
        assert "cache_control" not in b[1]
    assert not [w for w in result.warnings if w.startswith("brief:")]


def test_brief_problems_and_empty_brief(case):
    assert co.brief_problems(case, valid_brief(case)) == []
    probs = co.brief_problems(case, "## Core message\nx\n\n## Section points\n- summary: a\n\n## Avoid\n- y")
    assert any("headings" in p for p in probs) and any("analysis, next" in p for p in probs)
    llm = FakeLLM(MODEL, script=["   "])
    result, _ = gen("p2-brief", case, llm)
    assert "brief: no text; sections written without a brief" in result.warnings
    assert all(len(blocks(r)) == 3 for r in llm.requests[1:])  # frame, context, per-prompt
    llm = FakeLLM(MODEL, script=["Just some prose."])
    result, _ = gen("p2-brief", case, llm)
    assert any(w.startswith("brief: headings") for w in result.warnings)
    assert "<shared_brief>\nJust some prose." in blocks(llm.requests[1])[2]["text"]  # kept, with a warning


# ---------------------------------------------------------------- sequential flow (FR-COH-3)


def test_p3_runs_in_document_order_with_an_append_only_prefix(case):
    result, llm = gen("p3-flow", case)
    assert [call.node_id for call in llm.calls] == ["summary", "analysis", "next"]
    reqs = llm.requests
    assert len(blocks(reqs[0])) == 3 and breakpoints(reqs[0]) == 1  # nothing written yet
    assert breakpoints(reqs[1]) == breakpoints(reqs[2]) == 2
    for earlier, later in zip(reqs[1:], reqs[2:]):
        shared_a = [b["text"] for b in blocks(earlier)[:-1]]
        shared_b = [b["text"] for b in blocks(later)[:-1]]
        assert shared_b[:len(shared_a)] == shared_a and len(shared_b) == len(shared_a) + 1
    last = blocks(reqs[2])
    assert last[-2]["cache_control"] == {"type": "ephemeral"} and last[1]["cache_control"] == {"type": "ephemeral"}
    assert '<written_section id="summary"' in last[3]["text"] and '<written_section id="analysis"' in last[4]["text"]
    assert "| Revenue (USD) |" in last[3]["text"]  # data blocks rendered as Markdown in the document so far
    assert result.sections[0].model_text() in last[3]["text"]
    assert "<written_section title=" not in last[-1]["text"]  # depends_on text not repeated per prompt


def test_p3_cuts_the_oldest_sections_above_max_flow_chars(case):
    long = "First paragraph of the summary.\n\n" + ("More detail. " * 200)
    written = [(case.sections[0], long), (case.sections[1], "Analysis first.\n\n" + "x " * 300)]
    full = co.so_far_blocks(case, written, max_chars=100_000)
    assert "[Rest of this section omitted" not in "".join(full)
    cut = co.so_far_blocks(case, written, max_chars=1200)
    assert "First paragraph of the summary.\n\n[Rest of this section omitted for length.]" in cut[1]
    assert "More detail." not in cut[1] and "x x x" in cut[2]  # the newest stays whole
    llm = FakeLLM(MODEL, script=[long, "Analysis.", "Next."])
    asyncio.run(type(GENERATORS["p3-flow"])(max_flow_chars=500).generate(case, llm, 1))
    assert "[Rest of this section omitted" in blocks(llm.requests[2])[3]["text"]


# ---------------------------------------------------------------- repetition (FR-COH-5)


def test_repetition_finder_uses_8_grams_with_threshold_2():
    phrase8 = "the quarterly revenue target was missed by the sales team"
    words = phrase8.split()
    eight, nine = " ".join(words[:8]), " ".join(words[:9])
    assert co.find_repetition({"a": f"Intro. {eight}. End.", "b": f"Other. {eight}!"}) == []  # one shared 8-gram
    found = co.find_repetition({"a": f"Intro, {nine.upper()}.", "b": f"{nine} again", "c": "unrelated text here"})
    assert len(found) == 1 and (found[0].a, found[0].b, found[0].count) == ("a", "b", 2)
    assert found[0].phrase == " ".join(words[:8]) and "sections a and b" in found[0].warning()
    stop = "and the of to in is it that was for and the of to in is it"
    assert co.find_repetition({"a": stop, "b": stop}) == []  # stop-word-only n-grams are skipped
    assert co.find_repetition({"a": nine, "b": nine}, threshold=3) == []


# ---------------------------------------------------------------- editor (FR-COH-4)


def drafted(case):
    texts = {"summary": "Revenue was 4,210,000 this quarter. Margins held.",
             "analysis": "Revenue rose. Margin was 23.4%. Revenue rose.",
             "next": "- Hire\n- Ship\n- Sell"}
    return [c.section_out(case, s, texts[s.id], []) for s in case.sections]


def test_apply_edits_accepts_and_rejects_safely(case):
    sections = drafted(case)
    edits = [
        {"section_id": "summary", "find": "Margins held.", "replace": "Margins held steady.", "reason": "tone"},
        {"section_id": "analysis", "find": "Revenue rose.", "replace": "x", "reason": "twice"},
        {"section_id": "analysis", "find": "not there", "replace": "x"},
        {"section_id": "ghost", "find": "a", "replace": "b"},
        {"section_id": "summary", "find": "Revenue (USD)", "replace": "Sales"},  # only in the data block
        {"section_id": "next", "find": "Hire", "replace": "Hire 12 people"},  # adds a figure not in the data
        {"find": "x"},
        {"section_id": "summary", "find": "", "replace": "y"},
    ]
    before, warnings = co.apply_edits(case, sections, edits)
    assert sections[0].model_text() == "Revenue was 4,210,000 this quarter. Margins held steady."
    assert before == {"summary": "Revenue was 4,210,000 this quarter. Margins held."}
    assert sections[1].model_text() == "Revenue rose. Margin was 23.4%. Revenue rose."
    assert sections[2].model_text() == "- Hire\n- Ship\n- Sell"
    assert sections[0].blocks[0].origin == "data" and "Revenue (USD)" in sections[0].blocks[0].text  # read-only intact
    text = "\n".join(warnings)
    assert "applied 1 edit(s) to section summary" in text and "'Margins held.' -> 'Margins held steady.' (tone)" in text
    assert "edit 2 to section analysis: find matches 2 times" in text
    assert "edit 3 to section analysis: find matches 0 times" in text
    assert "unknown section 'ghost'" in text and "edit 5 to section summary: targets a read-only block" in text
    assert "reverted 1 edit(s) to section next: the edits add figures not in the data or the draft: 12" in text
    assert "edit 7: malformed" in text and "edit 8 to section summary: find matches 0 times" in text


def test_apply_edits_max_change_and_rechecks(case):
    sections = drafted(case)
    _, w = co.apply_edits(case, sections, [{"section_id": "summary", "find": "Margins held.",
                                            "replace": "Margins held, and the team shipped a great many things."}])
    assert "change ratio" in w[0] and "> 0.3" in w[0] and "Margins held." in sections[0].model_text()
    _, w = co.apply_edits(case, sections, [{"section_id": "analysis", "find": "Margin was", "replace": "Synergy: margin was"}])
    assert 'forbidden phrase "synergy"' in w[0]
    _, w = co.apply_edits(case, sections, [
        {"section_id": "summary", "find": "Margins held.", "replace": "Margins held at 23.4%."},  # 23.4 is in the data
        {"section_id": "summary", "find": "this quarter", "replace": "in Q3"},  # sequential edits on one section
    ])
    assert sections[0].model_text() == "Revenue was 4,210,000 in Q3. Margins held at 23.4%." and "applied 2 edit(s)" in w[0]
    _, w = co.apply_edits(case, sections, [{"section_id": "next", "find": "- Hire\n- Ship\n- Sell", "replace": " "}],
                          max_change=1.0)
    assert "empty the section" in w[0]


def test_parse_editor_reply():
    edits, notes = co.parse_editor_reply('```json\n{"edits": [{"section_id": "a", "find": "x", "replace": "y"}], "notes": ["n"]}\n```')
    assert edits[0]["find"] == "x" and notes == ["n"]
    assert co.parse_editor_reply('Here you go: {"edits": []}') == ([], [])
    for bad in ("no json", '{"edits": "nope"}', '{"edits": [], "notes": "x"}', "{bad json}"):
        with pytest.raises(ValueError):
            co.parse_editor_reply(bad)


def test_p4_applies_editor_edits_end_to_end(case):
    def edits(draft):
        first = draft["summary"].split(".")[0] + "."
        return [{"section_id": "summary", "find": first, "replace": first[:-1] + " overall.", "reason": "flow"}]

    result, llm = gen("p4-editor", case, FakeLLM(MODEL, responder=smart_responder(case, edits)))
    assert [call.role for call in llm.calls] == ["brief", "generate", "generate", "generate", "editor"]
    editor_req = llm.requests[-1]
    b = blocks(editor_req)
    assert [x["text"] for x in b[:3]] == [x["text"] for x in blocks(llm.requests[1])[:3]]  # same shared prefix
    assert breakpoints(editor_req) == 1
    per_prompt = b[-1]["text"]
    assert '<draft_section id="summary"' in per_prompt and '<read_only kind="metric">' in per_prompt
    assert "<repetition_findings>" in per_prompt and "30%" in per_prompt
    assert any(w.startswith("editor: applied 1 edit(s) to section summary") for w in result.warnings)
    assert "overall." in result.sections[0].model_text() and "overall." in result.markdown
    assert "editor note: checked the draft" in result.warnings


def test_editor_retry_then_success_and_unusable_reply(case):
    good = json.dumps({"edits": [], "notes": []})
    llm = FakeLLM(MODEL, script=[valid_brief(case), "s1", "s2", "s3", "not json", good])
    result, _ = gen("p4-editor", case, llm)
    assert [call.role for call in llm.calls][-2:] == ["editor", "editor"]
    retry = llm.requests[-1]["messages"]
    assert [m["role"] for m in retry] == ["user", "assistant", "user"] and retry[1]["content"] == "not json"
    assert "could not be used (no JSON object in the reply)" in retry[2]["content"]
    assert not any("unusable" in w for w in result.warnings)

    llm = FakeLLM(MODEL, script=[valid_brief(case), "s1", "s2", "s3", "nope", ""])
    result, _ = gen("p4-editor", case, llm)
    assert len(llm.calls) == 6 and llm.requests[-1]["messages"][1]["content"] == "nope"
    assert any("unusable reply after one retry" in w for w in result.warnings)
    assert [s.model_text() for s in result.sections] == ["s1", "s2", "s3"]  # unedited draft kept


def test_editor_refusal_leaves_draft_unedited_but_mismatch_fails_the_slot(case):
    llm = FakeLLM(MODEL, script=[valid_brief(case), "s1", "s2", "s3", FakeRefusal("cyber"), FakeRefusal("cyber")])
    result, _ = gen("p4-editor", case, llm)
    assert result.error is None and "editor: refused (cyber); draft left unedited" in result.warnings
    assert [call.status for call in llm.calls][-2:] == ["refusal", "refusal"]
    llm = FakeLLM(MODEL, script=[valid_brief(case), "s1", "s2", "s3", FakeReply("{}", served_model="other")])
    with pytest.raises(ServedModelMismatch):
        gen("p4-editor", case, llm)


# ---------------------------------------------------------------- error and refusal paths


@pytest.mark.parametrize("name", PROTOS)
def test_section_refusal_fails_the_slot(case, name):
    def respond(role, node_id, system, messages):
        if node_id == "analysis":
            return FakeRefusal("cyber")
        return smart_responder(case)(role, node_id, system, messages)

    llm = FakeLLM(MODEL, responder=respond)
    with pytest.raises(RefusalError):
        gen(name, case, llm)
    assert [call.status for call in llm.calls if call.node_id == "analysis"] == ["refusal", "refusal"]


def test_brief_refusal_fails_the_slot_before_any_section(case):
    llm = FakeLLM(MODEL, script=[FakeRefusal(), FakeRefusal()])
    with pytest.raises(RefusalError):
        gen("p2-brief", case, llm)
    assert [call.role for call in llm.calls] == ["brief", "brief"]


def test_empty_section_and_max_tokens_warnings(case):
    llm = FakeLLM(MODEL, script=[FakeReply("", stop_reason="max_tokens"), "two", "three"])
    result, _ = gen("p3-flow", case, llm)
    assert result.sections[0].blocks[-1].origin == "error"
    assert "section summary: stopped at max_tokens" in result.warnings and "section summary: no model text" in result.warnings


# ---------------------------------------------------------------- projection and report


def test_planned_calls_and_projection(case):
    n = len(case.sections)
    plans = {name: GENERATORS[name].planned_calls(case, 500) for name in PROTOS}
    assert [len(plans[p]) for p in PROTOS] == [n, n + 1, n, n + 2]
    assert plans["p3-flow"] == sorted(plans["p3-flow"]) and plans["p4-editor"][-1] > max(plans["p2-brief"])
    assert min(plans["p1-frame"]) > min(GENERATORS["b1-parallel"].planned_calls(case, 500))  # the frame costs input
    proj = runner.project([case], [GENERATORS[g] for g in ("b1-parallel", *PROTOS)], 1, CONFIG)
    labels = [ln.label for ln in proj.lines]
    assert all(p in labels for p in PROTOS) and "judge p4-editor vs b1-parallel" in labels
    assert "judge p4-editor vs b0-oneshot" not in labels  # b0 not in this run
    assert next(ln for ln in proj.lines if ln.label == "p4-editor").calls == n + 2
    assert any(a.startswith("prototypes:") for a in proj.assumptions)


def test_config_pairs_cover_every_prototype_against_b1_and_b0():
    pairs = {tuple(p) for p in CONFIG["judge"]["pairs"]}
    assert {(p, b) for p in PROTOS for b in ("b1-parallel", "b0-oneshot")} <= pairs


def _score(g, cid, metric, value, num=None, den=None):
    return {"generator": g, "case_id": cid, "rep": 1, "metric": metric, "value": value, "num": num, "den": den}


def test_ablation_deltas_layers_and_families():
    scores = [
        _score("b1-parallel", "w1", "xrep", 0.30), _score("p1-frame", "w1", "xrep", 0.10),
        _score("p2-brief", "w1", "xrep", 0.05), _score("b1-parallel", "q1", "xrep", 0.20),
        _score("p1-frame", "q1", "xrep", 0.20), _score("p2-brief", "q1", "xrep", 0.25),
        _score("b1-parallel", "w1", "unr", None, 1, 10), _score("p1-frame", "w1", "unr", None, 0, 10),
    ]
    abl = ablation(scores, [], ["b1-parallel", "p1-frame", "p2-brief"], {"w1": "f1_weekly", "q1": "f2_quarterly"}, CONFIG)
    assert abl["baselines"] == ["b1-parallel"] and abl["prototypes"] == ["p1-frame", "p2-brief"]
    assert list(abl["families"]) == ["all", "f1_weekly", "f2_quarterly"]
    w = abl["families"]["f1_weekly"]
    assert w["deltas"]["p1-frame"]["b1-parallel"]["xrep"] == pytest.approx(-0.20)
    assert w["deltas"]["p1-frame"]["b1-parallel"]["unr"] == pytest.approx(-0.10)
    assert [(ly["layer"], ly["over"]) for ly in w["layers"]] == [("frame", "b1-parallel"), ("shared brief", "p1-frame")]
    assert w["layers"][1]["deltas"]["xrep"] == pytest.approx(-0.05)
    assert abl["families"]["all"]["deltas"]["p2-brief"]["b1-parallel"]["xrep"] == pytest.approx(-0.10)
    text = "\n".join(render_ablation(abl))
    assert "## Coherence ablation (EVAL-4)" in text and "### Family `f2_quarterly` (1 case(s))" in text
    assert "| XREP | lower | -0.200 | -0.250 |" in text and "| UNR | lower | -10.0 pp | — |" in text
    assert "Not judged" in text
    assert ablation(scores, [], ["b1-parallel", "b2-chain"], {}, CONFIG) is None
    assert ablation(scores, [], ["p1-frame"], {}, CONFIG) is None


def test_fake_all_with_prototypes_end_to_end(tmp_path):
    try:
        from evals.graders import ALL_GRADERS  # noqa: F401

        cases = runner.select_cases("dev")
    except (ImportError, SystemExit) as e:
        pytest.skip(f"real graders or dev cases not available: {e}")
    by_family = {}
    for case_ in cases:
        by_family.setdefault(case_.family, case_.id)
    ids = list(by_family.values())[:2]
    gens = ["b0-oneshot", "b1-parallel", *PROTOS]
    args = ["--reps", "1", "--cases", *ids, "--generators", *gens, "--run-id", "fake-protos",
            "--runs-dir", str(tmp_path / "runs"), "--scoreboard-dir", str(tmp_path / "sb")]
    assert main(["all", *args]) == 0
    run = tmp_path / "runs" / "fake-protos"
    manifest = json.loads((run / "manifest.json").read_text())
    assert [g["name"] for g in manifest["generators"]] == gens
    calls = [json.loads(line) for line in (run / "calls.jsonl").read_text().splitlines()]
    roles = {(c_["generator"], c_["role"]) for c_ in calls if c_["phase"] == "generate"}
    assert ("p2-brief", "brief") in roles and ("p4-editor", "editor") in roles and ("p1-frame", "brief") not in roles
    judged = {tuple(json.loads(line)["pair"]) for line in (run / "judge.jsonl").read_text().splitlines()}
    assert {(p, b) for p in PROTOS for b in ("b1-parallel", "b0-oneshot")} <= judged
    report = (run / "report.md").read_text()
    assert "## Coherence ablation (EVAL-4)" in report and "### All families (2 case(s))" in report
    for fam in list(by_family)[:2]:
        assert f"### Family `{fam}` (1 case(s))" in report
    assert "| p4-editor vs b0-oneshot |" in report and "editor (p4-editor − p2-brief)" in report
    summary = json.loads((tmp_path / "sb" / "fake-protos.summary.json").read_text())
    assert set(summary["ablation"]["families"]) == {"all", *list(by_family)[:2]}
    assert not any(r.error for r in RunStore("fake-protos", tmp_path / "runs").iter_results())
