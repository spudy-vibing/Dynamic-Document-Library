"""The unofficial Claude Code backend, against a stubbed ``claude -p`` process (no network)."""

from __future__ import annotations

import asyncio
import json

import pytest

from evals import runner
from evals.core.config import load_config
from evals.core.llm import ClaudeCodeLLM, ServedModelMismatch, cli_env, cli_prompt
from evals.core.types import InfraError, RefusalError

CONFIG = load_config()
MODEL = CONFIG["models"]["generator"]


def ok(text: str = "Hello.", *, model: str = MODEL, stop: str = "end_turn") -> tuple[int, str, str]:
    body = {
        "type": "result", "subtype": "success", "is_error": False, "result": text, "stop_reason": stop,
        "api_error_status": None,
        "usage": {"input_tokens": 10, "output_tokens": 20, "cache_read_input_tokens": 5, "cache_creation_input_tokens": 600},
        "modelUsage": {model: {"inputTokens": 10, "outputTokens": 20}},
    }
    return 0, json.dumps(body), ""


def api_fail(status: int | None, text: str = "boom") -> tuple[int, str, str]:
    body = {"type": "result", "is_error": True, "result": text, "api_error_status": status}
    return 1, json.dumps(body), ""


class StubCli:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls: list[dict] = []

    async def __call__(self, argv, stdin, env, cwd):
        self.calls.append({"argv": argv, "stdin": stdin, "env": env, "cwd": cwd})
        return self.outcomes.pop(0)


async def _no_sleep(_: float) -> None:
    return None


def make(outcomes):
    stub = StubCli(outcomes)
    return ClaudeCodeLLM(MODEL, "medium", 16000, CONFIG, runner=stub, sleep=_no_sleep), stub


def complete(llm, messages=None):
    messages = messages or [{"role": "user", "content": "Write it."}]
    return asyncio.run(llm.complete(role="generate", node_id="s1", system="SYS", messages=messages))


def test_ok_call_records_usage_and_flags():
    llm, stub = make([ok("Section text.")])
    resp = complete(llm)
    assert resp.text == "Section text."
    call = resp.call
    assert (call.status, call.served_model, call.input_tokens, call.output_tokens) == ("ok", MODEL, 10, 20)
    assert (call.cache_read_tokens, call.cache_write_tokens) == (5, 600)
    assert call.cost_usd and call.cost_usd > 0
    argv = stub.calls[0]["argv"]
    assert argv[:2] == ["claude", "--print"]
    for flag, value in (("--model", MODEL), ("--effort", "medium"), ("--system-prompt", "SYS"), ("--tools", "")):
        assert argv[argv.index(flag) + 1] == value
    assert "--fallback-model" not in argv
    assert stub.calls[0]["stdin"] == "Write it."


def test_child_env_has_no_api_key_and_sets_max_tokens(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")
    env = cli_env(1234)
    assert "ANTHROPIC_API_KEY" not in env
    assert env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "1234"


def test_multi_turn_history_is_flattened():
    msgs = [
        {"role": "user", "content": [{"type": "text", "text": "First ask", "cache_control": {"type": "ephemeral"}}]},
        {"role": "assistant", "content": "First answer"},
        {"role": "user", "content": "Second ask"},
    ]
    prompt = cli_prompt(msgs)
    assert '<turn role="user">\nFirst ask\n</turn>' in prompt
    assert '<turn role="assistant">\nFirst answer\n</turn>' in prompt
    assert prompt.endswith("Second ask")


def test_served_model_mismatch_is_infra_error():
    llm, _ = make([ok(model="claude-haiku-5-5")])
    with pytest.raises(ServedModelMismatch):
        complete(llm)


def test_refusal_retried_once_then_raises():
    llm, stub = make([ok("", stop="refusal"), ok("", stop="refusal")])
    with pytest.raises(RefusalError):
        complete(llm)
    assert len(stub.calls) == 2 and [c.status for c in llm.calls] == ["refusal", "refusal"]


def test_overload_is_retried_then_succeeds():
    llm, stub = make([api_fail(529), api_fail(429), ok("Fine.")])
    assert complete(llm).text == "Fine."
    assert len(stub.calls) == 3 and llm.calls[-1].attempts == 3


def test_cli_usage_error_fails_fast():
    llm, stub = make([(1, "", "error: unknown option '--bogus'")])
    with pytest.raises(InfraError, match="unknown option"):
        complete(llm)
    assert len(stub.calls) == 1 and llm.calls[-1].status == "failed"


def test_factory_picks_cli_backend():
    llm = runner.make_llm_factory(CONFIG, live=False, cli=True)()
    assert isinstance(llm, ClaudeCodeLLM)
