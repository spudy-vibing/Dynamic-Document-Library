"""EVAL-21: the LLM wrapper, against a stubbed client (no network)."""

from __future__ import annotations

import asyncio

import pytest

from evals.core.config import cost_usd, load_config
from evals.core.llm import (
    AnthropicLLM,
    FakeLLM,
    FakeRefusal,
    FakeReply,
    ServedModelMismatch,
    build_params,
    check_params,
)
from evals.core.types import InfraError, RefusalError
from evals.tests.fakes import StubClient, api_error, stub_message

CONFIG = load_config()
MODEL = CONFIG["models"]["generator"]
MSGS = [{"role": "user", "content": "Write it."}]


async def _no_sleep(_: float) -> None:
    return None


def make(outcomes, *, stream: bool = True, **kw) -> tuple[AnthropicLLM, StubClient]:
    client = StubClient(outcomes)
    llm = AnthropicLLM(MODEL, "medium", 16000, CONFIG, client=client, sleep=_no_sleep, stream=stream, **kw)
    return llm, client


def complete(llm, **kw):
    return asyncio.run(llm.complete(role="generate", node_id="s1", system="sys", messages=MSGS, **kw))


@pytest.mark.parametrize("stream", [True, False])
def test_eval_21_effort_explicit_and_no_sampling_or_fallbacks(stream):
    llm, client = make([stub_message("ok")], stream=stream)
    complete(llm)
    (req,) = client.messages.requests
    assert req["output_config"] == {"effort": "medium"}
    assert req["model"] == MODEL and req["max_tokens"] == 16000 and req["system"] == "sys"
    for banned in ("temperature", "top_p", "top_k", "thinking", "fallbacks", "betas", "extra_body", "extra_headers"):
        assert banned not in req
    assert set(req) == {"model", "max_tokens", "messages", "output_config", "system"}


def test_check_params_rejects_sampling_and_fallbacks():
    good = build_params(model=MODEL, effort="low", max_tokens=10, system="", messages=MSGS)
    assert "system" not in good
    for bad in ({"temperature": 0.2}, {"top_p": 0.9}, {"fallbacks": [{"model": "x"}]}, {"thinking": {"type": "disabled"}}):
        with pytest.raises(InfraError):
            check_params({**good, **bad})
    with pytest.raises(InfraError):
        check_params({**good, "output_config": {}})


def test_usage_to_cost_and_text_blocks_only():
    msg = stub_message("Part one. ", input_tokens=1000, output_tokens=500, cache_read=2000, cache_write=300)
    msg.content.append(type(msg.content[0])(type="text", text="Part two."))
    llm, _ = make([msg])
    resp = complete(llm)
    assert resp.text == "Part one. Part two."  # thinking block skipped
    call = llm.calls[0]
    assert (call.input_tokens, call.output_tokens, call.cache_read_tokens, call.cache_write_tokens) == (1000, 500, 2000, 300)
    expected = cost_usd(MODEL, input_tokens=1000, output_tokens=500, cache_read_tokens=2000, cache_write_tokens=300, config=CONFIG)
    assert call.cost_usd == pytest.approx(expected)
    assert call.status == "ok" and call.served_model == MODEL and call.effort == "medium" and call.attempts == 1


def test_unpriced_model_cost_is_none():
    client = StubClient([stub_message("x", model="claude-unpriced-1")])
    llm = AnthropicLLM("claude-unpriced-1", "low", 100, CONFIG, client=client)
    asyncio.run(llm.complete(role="r", node_id=None, system="", messages=MSGS))
    assert llm.calls[0].cost_usd is None


def test_served_model_mismatch_is_infra_error():
    llm, _ = make([stub_message("ok", model="claude-other-model")])
    with pytest.raises(ServedModelMismatch) as e:
        complete(llm)
    assert isinstance(e.value, InfraError)
    assert len(llm.calls) == 1 and llm.calls[0].status == "failed"
    assert llm.calls[0].served_model == "claude-other-model"


def test_refusal_retried_once_then_refusal_error():
    llm, client = make([
        stub_message("", stop_reason="refusal", category="cyber"),
        stub_message("", stop_reason="refusal", stop_details=False),  # stop_details may be None
    ])
    with pytest.raises(RefusalError) as e:
        complete(llm)
    assert len(client.messages.requests) == 2
    assert [c.status for c in llm.calls] == ["refusal", "refusal"]
    assert llm.calls[0].refusal_category == "cyber" and llm.calls[1].refusal_category is None
    assert e.value.call is llm.calls[1]


def test_refusal_then_success_returns_text_and_records_both():
    llm, _ = make([stub_message("", stop_reason="refusal", category="bio"), stub_message("fine")])
    assert complete(llm).text == "fine"
    assert [c.status for c in llm.calls] == ["refusal", "ok"]


def test_max_tokens_recorded_and_text_returned():
    llm, _ = make([stub_message("truncated te", stop_reason="max_tokens")])
    resp = complete(llm, max_tokens=50)
    assert resp.text == "truncated te"
    assert llm.calls[0].stop_reason == "max_tokens" and llm.calls[0].status == "ok"


def test_transient_errors_retried_with_attempts_counted():
    llm, client = make([api_error(529), api_error(429, "rate_limit_error"), stub_message("ok")])
    assert complete(llm).text == "ok"
    assert len(client.messages.requests) == 3
    assert len(llm.calls) == 1 and llm.calls[0].attempts == 3


def test_non_transient_error_not_retried():
    llm, client = make([api_error(400, "invalid_request_error")])
    with pytest.raises(InfraError):
        complete(llm)
    assert len(client.messages.requests) == 1
    assert llm.calls[0].status == "failed" and llm.calls[0].attempts == 1


def test_exhausted_retries_raise_infra_error():
    llm, client = make([api_error(500, "api_error")] * 3, max_attempts=3)
    with pytest.raises(InfraError):
        complete(llm)
    assert len(client.messages.requests) == 3
    assert llm.calls[0].status == "failed" and llm.calls[0].attempts == 3


def test_make_client_disables_sdk_retries(monkeypatch):
    from evals.core.llm import make_client

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-not-a-real-key")
    client = make_client()
    assert client.max_retries == 0


# ---------------------------------------------------------------- FakeLLM


def test_fake_llm_is_deterministic_and_records_calls():
    msgs = [{"role": "user", "content": 'Data: <data>{"x": 4210000, "y": 23.4}</data> Write the section titled "Summary".'}]
    a, b = FakeLLM(MODEL, "medium", 100, CONFIG), FakeLLM(MODEL, "medium", 100, CONFIG)
    ra = asyncio.run(a.complete(role="generate", node_id="s", system="sys", messages=msgs))
    rb = asyncio.run(b.complete(role="generate", node_id="s", system="sys", messages=msgs))
    assert ra.text == rb.text and "summary" in ra.text.lower()
    call = a.calls[0]
    assert call.served_model == call.requested_model == MODEL and call.cost_usd is not None
    assert a.requests[0]["output_config"] == {"effort": "medium"}


def test_fake_llm_scripted_refusal_and_mismatch():
    llm = FakeLLM(MODEL, "medium", 100, CONFIG, script=[FakeRefusal("cyber"), FakeRefusal(None)])
    with pytest.raises(RefusalError):
        asyncio.run(llm.complete(role="g", node_id=None, system="", messages=MSGS))
    assert [c.status for c in llm.calls] == ["refusal", "refusal"]
    llm = FakeLLM(MODEL, "medium", 100, CONFIG, script=[FakeReply("x", served_model="other")])
    with pytest.raises(ServedModelMismatch):
        asyncio.run(llm.complete(role="g", node_id=None, system="", messages=MSGS))


def test_fake_llm_simulates_prompt_cache():
    block = {"type": "text", "text": "D" * 4000, "cache_control": {"type": "ephemeral"}}
    msgs = [{"role": "user", "content": [block, {"type": "text", "text": "q"}]}]
    llm = FakeLLM(MODEL, "medium", 100, CONFIG)
    for _ in range(2):
        asyncio.run(llm.complete(role="g", node_id=None, system="", messages=msgs))
    first, second = llm.calls
    assert first.cache_write_tokens > 0 and first.cache_read_tokens == 0
    assert second.cache_read_tokens == first.cache_write_tokens and second.cache_write_tokens == 0
