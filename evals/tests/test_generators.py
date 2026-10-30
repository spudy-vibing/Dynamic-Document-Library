"""EVAL-3: baselines B0-B2 under FakeLLM (no network)."""

from __future__ import annotations

import asyncio

import pytest

from evals.core.llm import FakeLLM, FakeRefusal, default_fake_responder
from evals.core.types import RefusalError
from evals.generators import GENERATORS
from evals.generators.common import PROMPTS_DIR, load_prompt, split_document
from evals.tests.fakes import write_tiny_case

MODEL = "claude-sonnet-5-5"


@pytest.fixture
def case(tmp_path):
    return write_tiny_case(tmp_path)


def gen(name, case, llm=None):
    llm = llm or FakeLLM(MODEL)
    return asyncio.run(GENERATORS[name].generate(case, llm, 1)), llm


def user_texts(request):
    out = []
    for m in request["messages"]:
        if m["role"] != "user":
            continue
        c = m["content"]
        out.append(c if isinstance(c, str) else "\n".join(b["text"] for b in c))
    return out


def test_registry_and_versions():
    for name in ("b0-oneshot", "b1-parallel", "b2-chain"):
        g = GENERATORS[name]
        assert g.name == name
        assert "@1-" in g.version and "_shared@" in g.version  # prompt version + hash


def test_prompt_files_have_version_lines():
    for p in PROMPTS_DIR.glob("*.md"):
        assert load_prompt(p.stem).version


@pytest.mark.parametrize("name", ["b0-oneshot", "b1-parallel", "b2-chain"])
def test_eval_3_common_shape(case, name):
    result, llm = gen(name, case)
    assert [s.id for s in result.sections] == ["summary", "analysis", "next"]
    summary, analysis, _ = result.sections
    assert summary.blocks[0].origin == "data" and summary.blocks[0].kind == "metric"
    assert analysis.blocks[0].origin == "data" and analysis.blocks[0].kind == "table"
    assert all(s.blocks[-1].origin == "model" and s.blocks[-1].text.strip() for s in result.sections)
    assert result.markdown.startswith("# Tinyco Q3 Report") and "## Financial Analysis" in result.markdown
    assert result.calls == llm.calls and result.wall_ms >= 0 and result.error is None
    assert result.generator == name and result.generator_version == GENERATORS[name].version
    for req in llm.requests:
        text = "\n".join(user_texts(req))
        assert "inserted automatically" in text.lower()  # data blocks are not to be reproduced
        assert req["output_config"] == {"effort": "medium"}
        first = req["messages"][0]["content"][0]
        assert first["cache_control"] == {"type": "ephemeral"} and "<data>" in first["text"] and "notes.md" in first["text"]
    # The cached context block is byte-identical for every call of a document.
    assert len({req["messages"][0]["content"][0]["text"] for req in llm.requests}) == 1


def test_b0_one_call_whole_outline(case):
    result, llm = gen("b0-oneshot", case)
    assert len(llm.requests) == 1
    text = user_texts(llm.requests[0])[0]
    for s in case.sections:
        assert f'title="{s.title}"' in text and s.instruction in text
    assert case.purpose in text and case.style in text


def test_b0_split_rules(case):
    raw = "\n".join([
        "Sure! Here is the report.",
        "# Tinyco Q3 Report",
        "## 2. Financial Analysis",
        "Revenue rose.",
        "```",
        "## Executive Summary",
        "```",
        "## Extra Heading",
        "More analysis.",
        "### Executive Summary",
        "Summary text.",
    ])
    sections, warnings = split_document(case, raw)
    assert [s.id for s in sections] == ["analysis", "summary", "next"]  # model order; missing appended
    analysis = sections[0].model_text()
    assert analysis.startswith("Sure! Here is the report.\n# Tinyco Q3 Report")  # preamble kept, not cleaned
    assert "## Executive Summary" in analysis  # heading inside a code fence is not a boundary
    assert "## Extra Heading" in analysis  # unmatched headings stay as text
    assert sections[1].model_text() == "Summary text."
    nxt = sections[2]
    assert nxt.blocks[-1].origin == "error" and nxt.blocks[-1].text == ""
    assert any("missing" in w for w in warnings) and any("out of outline order" in w for w in warnings)
    assert any("heading level 3" in w for w in warnings)


def test_b0_no_headings_goes_to_first_section(case):
    sections, warnings = split_document(case, "Just prose.")
    assert sections[0].id == "summary" and sections[0].model_text() == "Just prose."
    assert [s.blocks[-1].origin for s in sections[1:]] == ["error", "error"]


def test_b1_sections_isolated_and_dependencies_respected(case):
    order = []

    def responder(role, node_id, system, messages):
        order.append(node_id)
        return f"TEXT-OF-{node_id} " + default_fake_responder(role, node_id, system, messages)

    llm = FakeLLM(MODEL, responder=responder)
    result, _ = gen("b1-parallel", case, llm)
    assert len(llm.requests) == 3
    assert order.index("summary") < order.index("analysis") < order.index("next")
    by_node = {c.node_id: r for c, r in zip(llm.calls, llm.requests)}
    summary_text = user_texts(by_node["summary"])[0]
    assert "Financial Analysis" not in summary_text and "Next Steps" not in summary_text  # no outline
    assert case.purpose not in summary_text
    next_text = user_texts(by_node["next"])[0]
    assert "TEXT-OF-summary" in next_text and "TEXT-OF-analysis" in next_text
    assert "TEXT-OF-summary" in user_texts(by_node["analysis"])[0]
    assert "TEXT-OF-next" not in user_texts(by_node["analysis"])[0]


def test_b1_independent_sections_run_concurrently(tmp_path):
    case = write_tiny_case(tmp_path)
    from dataclasses import replace

    case = replace(case, sections=tuple(replace(s, depends_on=()) for s in case.sections))
    started, release = [], asyncio.Event()

    class Gate(FakeLLM):
        async def _call(self, **kw):
            started.append(kw["node_id"])
            if len(started) == 3:
                release.set()
            await asyncio.wait_for(release.wait(), 1)
            return await super()._call(**kw)

    result, _ = gen("b1-parallel", case, Gate(MODEL))
    assert sorted(started) == ["analysis", "next", "summary"]


def test_b1_refusal_propagates(case):
    llm = FakeLLM(MODEL, script=[FakeRefusal(), FakeRefusal()])
    with pytest.raises(RefusalError):
        gen("b1-parallel", case, llm)


def test_b2_append_only_history(case):
    result, llm = gen("b2-chain", case)
    reqs = llm.requests
    assert len(reqs) == 3
    for i, req in enumerate(reqs):
        assert [m["role"] for m in req["messages"]] == ["user", "assistant"] * i + ["user"]
        assert req["system"] == reqs[0]["system"]
    for prev, cur in zip(reqs, reqs[1:]):
        assert cur["messages"][: len(prev["messages"])] == prev["messages"]  # strictly appended
    for i, call in enumerate(llm.calls[:-1]):
        assert reqs[i + 1]["messages"][2 * i + 1]["content"] == result.sections[i].model_text()
    first = user_texts(reqs[0])[0]
    assert "<outline>" in first and case.purpose in first and 'section titled "Executive Summary"' in first
    assert reqs[1]["messages"][-1]["content"].startswith("Next, write section 2 of 3")


def test_b2_empty_reply_kept_valid(case):
    llm = FakeLLM(MODEL, script=["", "two", "three"])
    result, _ = gen("b2-chain", case, llm)
    assert llm.requests[1]["messages"][1]["content"] == "[no text returned]"
    assert result.sections[0].blocks[-1].origin == "error"


def test_planned_calls(case):
    assert len(GENERATORS["b0-oneshot"].planned_calls(case, 500)) == 1
    b1 = GENERATORS["b1-parallel"].planned_calls(case, 500)
    b2 = GENERATORS["b2-chain"].planned_calls(case, 500)
    assert len(b1) == len(b2) == 3 and b2 == sorted(b2)
