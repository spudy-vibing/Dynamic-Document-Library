"""Smoke tests for the WP-002 spike. Names carry BRD IDs per plan §0.5."""

from __future__ import annotations

import importlib.util
import time
from pathlib import Path

import pytest

import doccode
from doccode import Document, MockProvider, Prompt, Value, ctx
from doccode import _core
from doccode.errors import (
    ConfigError, DocumentFrozenError, DuplicateIdError, ExecutionError, TokenMisuseError,
    UnknownPlaceholderError,
)

EX = Path(__file__).resolve().parents[1] / "examples"


def load_example(name: str):
    spec = importlib.util.spec_from_file_location(name, EX / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ------------------------------------------------------------------ end to end

def test_uc1_weekly_status_end_to_end(tmp_path):
    doc = load_example("01_weekly_status").build()
    mock = MockProvider(EX / "01_weekly_status.mock.json")
    content = doc.execute(provider=mock)
    assert [s.id for s in content.sections] == ["shipped", "in-progress", "blockers", "next-week"]
    # next-week ran last and received all three upstream outputs
    last = mock.calls[-1]
    assert last.metadata["node_id"] == "next-week"
    assert "Billing export v2" in last.user_text and "[mock:blockers]" in last.user_text
    md = content.save(tmp_path / "w.md").read_text()
    html = content.save(tmp_path / "w.html").read_text()
    assert md.startswith("# Platform Team Weekly Status") and "## Next Week" in md
    assert "<h2>Next Week</h2>" in html


def test_brd7_reference_example_with_expansion(tmp_path):
    doc = load_example("02_quarterly_report").build()
    mock = MockProvider(EX / "02_quarterly_report.mock.json")
    content = doc.execute(provider=mock, max_concurrency=4)
    themes = content.find("themes")
    assert [s.id for s in themes.subsections] == [
        "by-theme/pricing-pressure", "by-theme/enterprise-growth", "by-theme/hiring"]
    assert content.find("executive-summary").blocks[1].value == "25%"  # Value-backed metric
    # FR-COH-1 frame: outsiders see the placeholder line, expansion prompts see siblings
    reqs = {r.metadata["node_id"]: r for r in mock.calls}
    outsider = dict(reqs["summary"].prefix)["frame"]
    insider = dict(reqs["by-theme/hiring.1"].prefix)["frame"]
    assert "Themes — subsections generated at run time" in outsider
    assert "- Pricing pressure" in insider and "generated at run time" not in insider
    assert 'You are writing section 2.3 "Hiring".' in reqs["by-theme/hiring.1"].user_text
    assert "Executive Summary" in content.save(tmp_path / "q3.html").read_text()


def test_brd7_example_collides_under_brd_shared_namespace(monkeypatch):
    """Finding F1: with FR-DAG-1's single namespace, §7 itself raises DuplicateIdError."""
    monkeypatch.setattr(_core, "SHARED_NAMESPACE", True)
    with pytest.raises(DuplicateIdError, match="'risks'"):
        load_example("02_quarterly_report").build()


# ------------------------------------------------------------------ scheduling

def test_fr_exe_3_slow_prompt_does_not_delay_unrelated_downstream():
    doc = Document("t", coherence="off")
    with doc.section("A"):
        slow = Prompt("slow", id="slow")
        fast = Prompt("fast", id="fast")
    with doc.section("B"):
        Prompt(f"after fast: {fast}", id="down")
        Prompt(f"after slow: {slow}", id="down-slow")
    doc.execute(provider=MockProvider(delays={"slow": 0.3}))
    t = {(ev, nid): at for ev, nid, at in doc._last_run.trace}
    assert t[("end", "down")] < t[("end", "slow")]


# ------------------------------------------------------------------ tokens and pitfalls

def test_fr_ref_9_format_spec_and_repr_misuse():
    doc = Document("t")
    with doc.section("S"):
        v = Value(lambda: 0.25, id="g")
        with pytest.raises(TypeError, match="format spec"):
            Prompt(f"growth {v:.0%}")             # natural for a Value, but rejected
        Prompt(f"bad {v!r}", id="bad")
    with pytest.raises(TokenMisuseError):
        doc.plan()


def test_fr_ref_8_plain_string_with_python_name_gives_hint():
    doc = Document("t")
    with doc.section("S"):
        summary = Prompt("x", id="summary")
        Prompt("Based on {summary}, continue.", id="next")   # forgot the f
    with pytest.raises(UnknownPlaceholderError, match="f-string"):
        doc.plan()
    assert summary  # silence unused


def test_fr_ref_8_token_mode_sends_braces_and_placeholders_literally():
    doc = Document("t", context={"company": "Acme"}, coherence="off")
    with doc.section("S"):
        a = Prompt("a", id="a")
        Prompt(f'For {ctx.company} use {a}; JSON like {{"k": 1}}; and {{company}}', id="b")
    mock = MockProvider({"a": "out {data} {{ "})
    doc.execute(provider=mock)
    sent = mock.calls[-1].metadata["instruction"]
    # FR-REF-3: inserted text not re-scanned; FR-REF-8: '{company}' sent literally (silent!)
    assert sent == 'For Acme use out {data} {{; JSON like {"k": 1}; and {company}'


def test_fr_ctx_2_prompt_in_context_is_rejected():
    """Finding F6: a Prompt/Value passed via context= adds no edge; the spike rejects it."""
    doc = Document("t")
    with doc.section("S"):
        a = Prompt("a", id="a")
        Prompt("Write about {thing}", context={"thing": a}, id="b")
    with pytest.raises(TokenMisuseError, match="refs="):
        doc.plan()


def test_fr_prm_10_prompt_created_before_with_block_warns():
    doc = Document("t")
    early = Prompt("I meant to be in section S", id="early")
    with doc.section("S"):
        Prompt("x")
    with pytest.warns(doccode.OrphanPromptWarning, match="early"):
        doc.execute(provider=MockProvider())
    assert early.id == "early" and early.doc is None


def test_fr_prv_4_unknown_mock_key_is_config_error():
    doc = Document("t")
    with doc.section("S"):
        Prompt("x", id="real")
    with pytest.raises(ConfigError, match="did you mean 'real'"):
        doc.execute(provider=MockProvider({"reel": "x"}))


def test_fr_doc_9_frozen_after_execute():
    doc = Document("t")
    with doc.section("S"):
        Prompt("x")
    doc.execute(provider=MockProvider())
    with pytest.raises(DocumentFrozenError):
        doc.section("late")


def test_fr_exe_4_expansion_failure_without_mock_map():
    """Finding: the §7 doc fails under a bare MockProvider (the outline is not a list)."""
    doc = load_example("02_quarterly_report").build()
    with pytest.raises(ExecutionError, match="by-theme"):
        doc.execute(provider=MockProvider())


def test_no_provider_is_config_error():
    doc = Document("t")
    with doc.section("S"):
        Prompt("x")
    with pytest.raises(ConfigError, match="dry-run"):
        doc.execute()


def test_public_surface():
    for name in ("Document", "Prompt", "Value", "ctx", "checks", "parse", "MockProvider"):
        assert hasattr(doccode, name)
    _ = time
