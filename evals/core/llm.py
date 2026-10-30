"""Harness-owned model access (EVAL-21, EVALS_PROPOSAL §4.1).

``AnthropicLLM`` wraps the official ``anthropic`` async SDK. ``ClaudeCodeLLM`` runs
headless ``claude -p`` on the owner's subscription (unofficial runs only). ``FakeLLM`` is a
deterministic stand-in for offline runs and tests. Both share the refusal policy:
a refusal is recorded, retried once, and a second refusal raises ``RefusalError``.

Rules enforced here, for every call:
- effort is sent explicitly as ``output_config={"effort": ...}``;
- no ``temperature``/``top_p``/``top_k``, no ``thinking`` configuration (models use
  their adaptive default), no ``fallbacks`` parameter or beta header, no refusal-
  fallback middleware;
- the served model must equal the requested model (else ``ServedModelMismatch``);
- usage (and so cost) comes from the response, never from string length.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import random
import re
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from evals.core.config import cost_usd, load_config
from evals.core.types import CallRecord, InfraError, LLMResponse, RefusalError

#: Request keys the wrapper must never send (EVAL-21). Checked before every call.
FORBIDDEN_PARAMS = frozenset({"temperature", "top_p", "top_k", "thinking", "fallbacks", "betas"})

#: HTTP statuses retried with backoff. 529 is "overloaded".
TRANSIENT_STATUS = frozenset({408, 409, 429, 500, 502, 503, 504, 529})
TRANSIENT_ERROR_TYPES = frozenset({"overloaded_error", "api_error", "rate_limit_error", "timeout_error"})

DEFAULT_MAX_ATTEMPTS = 6  # per logical call; the SDK's own retries are disabled so every attempt is counted
DEFAULT_BACKOFF_S = 2.0
MAX_BACKOFF_S = 60.0
REQUEST_TIMEOUT_S = 900.0  # streaming is used, so this is a generous idle/read bound


class ServedModelMismatch(InfraError):
    """The response came from a different model than requested (EVAL-21)."""


Sleep = Callable[[float], Awaitable[None]]


def check_params(params: dict[str, Any]) -> None:
    """Fail loudly if a request would break EVAL-21. Used by the wrapper and the batch judge."""
    bad = FORBIDDEN_PARAMS.intersection(params)
    if bad:
        raise InfraError(f"forbidden request parameters: {sorted(bad)}")
    if params.get("output_config", {}).get("effort") is None:
        raise InfraError("effort must be set explicitly in output_config")
    extra = params.get("extra_body") or {}
    if FORBIDDEN_PARAMS.intersection(extra):
        raise InfraError(f"forbidden request parameters in extra_body: {sorted(FORBIDDEN_PARAMS.intersection(extra))}")


def build_params(
    *, model: str, effort: str, max_tokens: int, system: str, messages: list[dict[str, Any]]
) -> dict[str, Any]:
    """The exact Messages API body the harness sends. Nothing else is ever added."""
    params: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": messages,
        "output_config": {"effort": effort},
    }
    if system:
        params["system"] = system
    check_params(params)
    return params


def response_text(message: Any) -> str:
    """Concatenate ``text`` blocks only (thinking and other blocks are skipped)."""
    parts = [getattr(b, "text", "") for b in (getattr(message, "content", None) or []) if getattr(b, "type", None) == "text"]
    return "".join(parts)


def record_from_message(
    message: Any,
    *,
    role: str,
    node_id: str | None,
    requested_model: str,
    effort: str,
    latency_ms: int,
    attempts: int,
    config: dict[str, Any] | None,
    cost_factor: float = 1.0,
) -> CallRecord:
    """Build a CallRecord from an SDK ``Message`` (or any object with the same fields).

    Status is "refusal" for ``stop_reason == "refusal"``, "failed" for a served-model
    mismatch, else "ok". ``cost_factor`` is 0.5 for Message Batches results.
    """
    usage = getattr(message, "usage", None)
    tok = {
        "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
        "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
        "cache_read_tokens": int(getattr(usage, "cache_read_input_tokens", 0) or 0),
        "cache_write_tokens": int(getattr(usage, "cache_creation_input_tokens", 0) or 0),
    }
    served = getattr(message, "model", None)
    stop_reason = getattr(message, "stop_reason", None)
    category = None
    status = "ok"
    if stop_reason == "refusal":
        status = "refusal"
        details = getattr(message, "stop_details", None)  # may be None
        category = getattr(details, "category", None) if details is not None else None
    if served != requested_model:
        status = "failed"
    cost = cost_usd(requested_model, config=config, **tok)
    if cost is not None:
        cost *= cost_factor
    return CallRecord(
        role=role,
        node_id=node_id,
        status=status,  # type: ignore[arg-type]
        requested_model=requested_model,
        served_model=served,
        effort=effort,
        stop_reason=stop_reason,
        refusal_category=category,
        latency_ms=latency_ms,
        attempts=attempts,
        cost_usd=cost,
        **tok,
    )


def failed_record(
    *, role: str, node_id: str | None, model: str, effort: str, latency_ms: int, attempts: int,
    config: dict[str, Any] | None,
) -> CallRecord:
    return CallRecord(
        role=role,
        node_id=node_id,
        status="failed",
        requested_model=model,
        served_model=None,
        effort=effort,
        stop_reason=None,
        refusal_category=None,
        input_tokens=0,
        output_tokens=0,
        cache_read_tokens=0,
        cache_write_tokens=0,
        latency_ms=latency_ms,
        attempts=attempts,
        cost_usd=cost_usd(model, input_tokens=0, output_tokens=0, config=config),
    )


def is_transient(exc: BaseException) -> bool:
    """429, 529/overloaded, 5xx, timeouts and connection errors are retried."""
    import anthropic

    if isinstance(exc, (anthropic.APIConnectionError, asyncio.TimeoutError)):
        return True  # APITimeoutError is a subclass of APIConnectionError
    if isinstance(exc, anthropic.APIStatusError):
        if exc.status_code in TRANSIENT_STATUS or exc.status_code >= 500:
            return True
        return getattr(exc, "type", None) in TRANSIENT_ERROR_TYPES
    if isinstance(exc, anthropic.APIError):  # e.g. an error event mid-stream
        body = getattr(exc, "body", None)
        etype = (body or {}).get("error", {}).get("type") if isinstance(body, dict) else None
        return etype in TRANSIENT_ERROR_TYPES
    return False


class _BaseLLM:
    """Shared refusal policy. Subclasses implement one logical call in ``_call``."""

    model: str
    effort: str
    max_tokens: int
    calls: list[CallRecord]

    async def complete(
        self,
        *,
        role: str,
        node_id: str | None,
        system: str,
        messages: list[dict[str, Any]],
        max_tokens: int | None = None,
    ) -> LLMResponse:
        mt = max_tokens or self.max_tokens
        refusals = 0
        while True:
            text, call = await self._call(role=role, node_id=node_id, system=system, messages=messages, max_tokens=mt)
            self.calls.append(call)
            if call.status == "failed":
                raise ServedModelMismatch(
                    f"requested {call.requested_model}, served {call.served_model} ({role}, {node_id})"
                )
            if call.status == "refusal":
                refusals += 1
                if refusals >= 2:
                    raise RefusalError(call)
                continue  # retry once
            return LLMResponse(text=text, call=call)

    async def _call(
        self, *, role: str, node_id: str | None, system: str, messages: list[dict[str, Any]], max_tokens: int
    ) -> tuple[str, CallRecord]:  # pragma: no cover - abstract
        raise NotImplementedError


class AnthropicLLM(_BaseLLM):
    """One instance per (generator, case, rep) slot; ``calls`` holds every call made.

    ``client`` is an ``anthropic.AsyncAnthropic`` (shared across slots by the runner);
    when omitted one is created with the SDK's retries disabled, so the wrapper's own
    backoff loop sees, and counts, every attempt. Requests are streamed
    (``messages.stream`` + ``get_final_message``): 16,000 max_tokens is close to the
    SDK's non-streaming guard and streaming avoids idle-connection drops.
    """

    def __init__(
        self,
        model: str,
        effort: str,
        max_tokens: int,
        config: dict[str, Any] | None = None,
        client: Any = None,
        *,
        limiter: asyncio.Semaphore | None = None,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        backoff_s: float = DEFAULT_BACKOFF_S,
        sleep: Sleep = asyncio.sleep,
        stream: bool = True,
    ) -> None:
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens
        self.config = config if config is not None else load_config()
        self.client = client if client is not None else make_client()
        self.limiter = limiter
        self.max_attempts = max_attempts
        self.backoff_s = backoff_s
        self.sleep = sleep
        self.stream = stream
        self.calls = []

    async def _send(self, params: dict[str, Any]) -> Any:
        if self.stream:
            async with self.client.messages.stream(**params) as s:
                return await s.get_final_message()
        return await self.client.messages.create(**params)

    async def _call(
        self, *, role: str, node_id: str | None, system: str, messages: list[dict[str, Any]], max_tokens: int
    ) -> tuple[str, CallRecord]:
        params = build_params(model=self.model, effort=self.effort, max_tokens=max_tokens, system=system, messages=messages)
        busy_ms = 0  # time spent in requests, excluding retry sleeps
        for attempt in range(1, self.max_attempts + 1):
            t0 = time.perf_counter()
            try:
                if self.limiter is not None:
                    async with self.limiter:
                        msg = await self._send(params)
                else:
                    msg = await self._send(params)
            except Exception as exc:  # noqa: BLE001 - classified below
                busy_ms += int((time.perf_counter() - t0) * 1000)
                if is_transient(exc) and attempt < self.max_attempts:
                    delay = min(MAX_BACKOFF_S, self.backoff_s * 2 ** (attempt - 1)) * (0.5 + random.random() / 2)
                    await self.sleep(delay)
                    continue
                self.calls.append(
                    failed_record(role=role, node_id=node_id, model=self.model, effort=self.effort,
                                  latency_ms=busy_ms, attempts=attempt, config=self.config)
                )
                raise InfraError(f"{type(exc).__name__} after {attempt} attempt(s): {exc}") from exc
            busy_ms += int((time.perf_counter() - t0) * 1000)
            call = record_from_message(
                msg, role=role, node_id=node_id, requested_model=self.model, effort=self.effort,
                latency_ms=busy_ms, attempts=attempt, config=self.config,
            )
            return response_text(msg), call
        raise AssertionError("unreachable")  # pragma: no cover


def make_client(*, sync: bool = False) -> Any:
    """An SDK client with retries disabled (the wrapper retries and counts attempts).

    Credentials come from the environment (``ANTHROPIC_API_KEY``) as the SDK resolves them;
    the harness never reads, prints or stores the key. No middleware is registered.
    """
    import anthropic

    cls = anthropic.Anthropic if sync else anthropic.AsyncAnthropic
    return cls(max_retries=0, timeout=anthropic.Timeout(REQUEST_TIMEOUT_S, connect=10.0))


# ---------------------------------------------------------------- claude code (unofficial)


#: Flags for every headless call: the eval's own system prompt replaces Claude Code's,
#: no tools, no settings, MCP servers, slash commands or saved session.
CLI_FLAGS = (
    "--print", "--output-format", "json", "--tools", "", "--no-session-persistence",
    "--setting-sources", "", "--strict-mcp-config", "--disable-slash-commands",
)

CliRunner = Callable[[list[str], str, dict[str, str], str], Awaitable[tuple[int, str, str]]]


async def run_cli_process(argv: list[str], stdin: str, env: dict[str, str], cwd: str) -> tuple[int, str, str]:
    """Run one ``claude -p`` call; returns (exit code, stdout, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        *argv, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        env=env, cwd=cwd,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(stdin.encode()), timeout=REQUEST_TIMEOUT_S)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise
    return proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")


def cli_prompt(messages: list[dict[str, Any]]) -> str:
    """The single user turn sent to ``claude -p``.

    One user message is sent as is (cache_control markers are dropped). A multi-turn
    history (B2's chat chain) is flattened into one message, earlier turns tagged by role,
    because headless mode takes one prompt per call. This is a known difference from API mode.
    """
    if len(messages) == 1:
        return message_text(messages[0]["content"])
    turns = [f'<turn role="{m["role"]}">\n{message_text(m["content"])}\n</turn>' for m in messages[:-1]]
    return (
        "<conversation_so_far>\n" + "\n".join(turns) + "\n</conversation_so_far>\n\n"
        + message_text(messages[-1]["content"])
    )


def cli_env(max_tokens: int) -> dict[str, str]:
    """The child environment: the subscription login, never an API key (so nothing is billed to one)."""
    import os

    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
    env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = str(max_tokens)
    return env


class ClaudeCodeLLM(_BaseLLM):
    """UNOFFICIAL backend: each call is one headless ``claude -p`` run on the owner's
    Claude Code subscription. No API key, nothing billed per token.

    Differences from ``AnthropicLLM`` (why results never count toward a gate):
    Claude Code adds some context of its own (about 600 tokens), ``max_tokens`` is set
    through ``CLAUDE_CODE_MAX_OUTPUT_TOKENS``, multi-turn histories are flattened
    (``cli_prompt``), cache breakpoints are Claude Code's, refusal categories are not
    reported, and ``cost_usd`` is the API-equivalent price of the reported tokens.
    The served model is still asserted, from ``modelUsage``.
    """

    def __init__(
        self,
        model: str,
        effort: str,
        max_tokens: int,
        config: dict[str, Any] | None = None,
        *,
        limiter: asyncio.Semaphore | None = None,
        executable: str = "claude",
        runner: CliRunner = run_cli_process,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        backoff_s: float = DEFAULT_BACKOFF_S,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens
        self.config = config if config is not None else load_config()
        self.limiter = limiter
        self.executable = executable
        self.runner = runner
        self.max_attempts = max_attempts
        self.backoff_s = backoff_s
        self.sleep = sleep
        self.calls = []
        self.requests: list[dict[str, Any]] = []

    def argv(self, system: str) -> list[str]:
        return [self.executable, *CLI_FLAGS, "--model", self.model, "--effort", self.effort, "--system-prompt", system]

    async def _run(self, argv: list[str], prompt: str, env: dict[str, str]) -> tuple[int, str, str]:
        import tempfile

        with tempfile.TemporaryDirectory(prefix="evals-cli-") as cwd:  # no CLAUDE.md or project files nearby
            if self.limiter is not None:
                async with self.limiter:
                    return await self.runner(argv, prompt, env, cwd)
            return await self.runner(argv, prompt, env, cwd)

    async def _call(
        self, *, role: str, node_id: str | None, system: str, messages: list[dict[str, Any]], max_tokens: int
    ) -> tuple[str, CallRecord]:
        build_params(model=self.model, effort=self.effort, max_tokens=max_tokens, system=system, messages=messages)
        argv, prompt, env = self.argv(system), cli_prompt(messages), cli_env(max_tokens)
        self.requests.append({"argv": argv, "prompt": prompt})
        busy_ms = 0
        for attempt in range(1, self.max_attempts + 1):
            t0 = time.perf_counter()
            reason = ""
            try:
                code, out, err = await self._run(argv, prompt, env)
                data = json.loads(out) if out.strip() else {}
            except (asyncio.TimeoutError, OSError, json.JSONDecodeError) as exc:
                code, data, err, reason = -1, {}, "", f"{type(exc).__name__}: {exc}"
            busy_ms += int((time.perf_counter() - t0) * 1000)
            ok = code == 0 and data.get("type") == "result" and not data.get("is_error")
            if ok:
                return data.get("result") or "", self._record(data, role=role, node_id=node_id,
                                                              latency_ms=busy_ms, attempts=attempt)
            status = data.get("api_error_status")
            # timeouts, unreadable output and 429/529/5xx are retried; a CLI usage error or a
            # failed login (non-zero exit, no API status) is not, so it fails fast
            transient = reason != "" or status in TRANSIENT_STATUS or (isinstance(status, int) and status >= 500)
            if transient and attempt < self.max_attempts:
                delay = min(MAX_BACKOFF_S, self.backoff_s * 2 ** (attempt - 1)) * (0.5 + random.random() / 2)
                await self.sleep(delay)
                continue
            self.calls.append(
                failed_record(role=role, node_id=node_id, model=self.model, effort=self.effort,
                              latency_ms=busy_ms, attempts=attempt, config=self.config)
            )
            detail = reason or str(data.get("result") or err.strip()[-300:] or f"exit code {code}")
            raise InfraError(f"claude -p failed after {attempt} attempt(s) (status {status}): {detail}")
        raise AssertionError("unreachable")  # pragma: no cover

    def _record(self, data: dict[str, Any], *, role: str, node_id: str | None, latency_ms: int, attempts: int) -> CallRecord:
        usage = data.get("usage") or {}
        tok = {
            "input_tokens": int(usage.get("input_tokens") or 0),
            "output_tokens": int(usage.get("output_tokens") or 0),
            "cache_read_tokens": int(usage.get("cache_read_input_tokens") or 0),
            "cache_write_tokens": int(usage.get("cache_creation_input_tokens") or 0),
        }
        models = sorted((data.get("modelUsage") or {}).keys())
        served = models[0] if len(models) == 1 else ",".join(models) or None
        stop = data.get("stop_reason")
        status = "refusal" if stop == "refusal" else "ok"
        if served != self.model:
            status = "failed"
        return CallRecord(
            role=role,
            node_id=node_id,
            status=status,  # type: ignore[arg-type]
            requested_model=self.model,
            served_model=served,
            effort=self.effort,
            stop_reason=stop,
            refusal_category=None,
            latency_ms=latency_ms,
            attempts=attempts,
            cost_usd=cost_usd(self.model, config=self.config, **tok),
            **tok,
        )


# ---------------------------------------------------------------- fake


@dataclass
class FakeRefusal:
    """A scripted refusal (``stop_reason: "refusal"``)."""

    category: str | None = "cyber"


@dataclass
class FakeReply:
    text: str
    stop_reason: str = "end_turn"
    served_model: str | None = None  # set to simulate a served-model mismatch


Responder = Callable[[str, str | None, str, list[dict[str, Any]]], "str | FakeReply | FakeRefusal"]

_TITLED = re.compile(r'section titled "([^"]+)"')
_OUTLINE = re.compile(r'<section\b[^>]*\btitle="([^"]+)"')
_NUMBER = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?(?![\w.])")


def message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(b.get("text", "") for b in content if isinstance(b, dict))


def fake_section_text(title: str, prompt: str, seed: str) -> str:
    """Two short paragraphs that mention the title and a few numbers from the prompt."""
    rng = random.Random(seed)
    data = prompt.split("<data>", 1)[-1].split("</data>", 1)[0] if "<data>" in prompt else prompt
    nums = [n for n in _NUMBER.findall(data) if not (n.isdigit() and int(n) <= 10)]
    picks = rng.sample(nums, k=min(3, len(nums))) if nums else []
    first = f"This part covers {title.lower()}."
    if picks:
        first += " The figures to note are " + ", ".join(picks) + "."
    openers = ["Overall the picture is mixed.", "The trend held through the period.", "Several items need follow-up."]
    fillers = [
        "Teams reported progress against the plan.",
        "The main open question is timing.",
        "Owners are named in the notes.",
        "No new risks were raised this period.",
    ]
    second = rng.choice(openers) + f" The {title.lower()} points above should be read with the data tables."
    second += "".join(" " + s for s in rng.sample(fillers, k=rng.randint(0, len(fillers))))
    if rng.random() < 0.3:  # an unsupported figure now and then, so fidelity graders have something to find
        second += f" That is roughly {rng.uniform(11, 99):.1f}% above the usual level."
    return f"{first}\n\n{second}"


_SECTION_IDS = re.compile(r"The section IDs are: ([^\n]+?)\.?$", re.M)


def fake_brief(last: str) -> str:
    """A valid shared brief (the four FR-COH-2 headings, one point per section ID in the request)."""
    m = _SECTION_IDS.search(last)
    ids = [s.strip() for s in m.group(1).split(",") if s.strip()] if m else []
    points = "\n".join(f"- {sid}: the main point of this section." for sid in ids)
    return (
        "## Core message\nThe period's results, stated plainly.\n\n"
        f"## Section points\n{points}\n\n"
        "## Terms\n- Use the metric names from the data.\n\n"
        "## Avoid\n- Repeating figures across sections."
    )


def default_fake_responder(role: str, node_id: str | None, system: str, messages: list[dict[str, Any]]) -> str:
    """Write one section if asked for 'the section titled "X"', else a whole document from the outline.

    The brief and editor roles get valid, minimal replies (a four-heading brief; no edits), as
    DocCode's MockProvider does (FR-PRV-4), so fake runs of the prototypes carry no spurious warnings.
    """
    last = message_text(messages[-1]["content"]) if messages else ""
    if role == "brief":
        return fake_brief(last)
    if role == "editor":
        return '{"edits": [], "notes": []}'

    everything = system + "\n" + "\n".join(message_text(m["content"]) for m in messages)
    seed = hashlib.sha256(everything.encode()).hexdigest()
    m = _TITLED.findall(last)
    if m:
        return fake_section_text(m[-1], everything, seed)
    titles = _OUTLINE.findall(last)
    if titles:
        return "\n\n".join(f"## {t}\n\n{fake_section_text(t, everything, seed + t)}" for t in titles)
    return fake_section_text("the request", everything, seed)


class FakeLLM(_BaseLLM):
    """Deterministic, offline ``LLM``.

    ``script`` (consumed in order, then falls back to ``responder``) holds strings,
    ``FakeReply`` or ``FakeRefusal`` items. Token counts are synthetic (chars / 4 plus
    a fixed thinking allowance); cache reads/writes are simulated for content blocks
    carrying ``cache_control`` so cache metrics have something to measure. Every request
    is kept in ``requests`` (deep-copied) for tests.
    """

    THINKING_TOKENS = 200

    def __init__(
        self,
        model: str = "fake-model",
        effort: str = "medium",
        max_tokens: int = 16000,
        config: dict[str, Any] | None = None,
        *,
        script: list[str | FakeReply | FakeRefusal] | None = None,
        responder: Responder | None = None,
        latency_ms: int = 5,
    ) -> None:
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens
        self.config = config
        self.script = list(script or [])
        self.responder = responder or default_fake_responder
        self.latency_ms = latency_ms
        self.calls = []
        self.requests: list[dict[str, Any]] = []
        self._cached_prefixes: set[str] = set()

    def _cache_usage(self, system: str, messages: list[dict[str, Any]]) -> tuple[int, int, int]:
        """(uncached input, cache read, cache write) tokens, simulating prefix caching."""
        prefix = system
        cached_upto = ""
        total = len(system)
        for m in messages:
            content = m["content"]
            blocks = [{"type": "text", "text": content}] if isinstance(content, str) else content
            for b in blocks:
                text = b.get("text", "") if isinstance(b, dict) else ""
                prefix += text
                total += len(text)
                if isinstance(b, dict) and b.get("cache_control"):
                    cached_upto = prefix
        if not cached_upto:
            return total // 4, 0, 0
        key = hashlib.sha256(cached_upto.encode()).hexdigest()
        cached_tokens = len(cached_upto) // 4
        rest = (total - len(cached_upto)) // 4
        if key in self._cached_prefixes:
            return rest, cached_tokens, 0
        self._cached_prefixes.add(key)
        return rest, 0, cached_tokens

    async def _call(
        self, *, role: str, node_id: str | None, system: str, messages: list[dict[str, Any]], max_tokens: int
    ) -> tuple[str, CallRecord]:
        params = build_params(model=self.model, effort=self.effort, max_tokens=max_tokens, system=system, messages=messages)
        self.requests.append(copy.deepcopy(params))
        item = self.script.pop(0) if self.script else self.responder(role, node_id, system, messages)
        if isinstance(item, str):
            item = FakeReply(item)
        uncached, read, write = self._cache_usage(system, messages)
        if isinstance(item, FakeRefusal):
            text, stop, served, category = "", "refusal", self.model, item.category
            status = "refusal"
        else:
            text, stop, served, category = item.text, item.stop_reason, item.served_model or self.model, None
            status = "ok" if served == self.model else "failed"
        tok = dict(
            input_tokens=uncached,
            output_tokens=len(text) // 4 + self.THINKING_TOKENS,
            cache_read_tokens=read,
            cache_write_tokens=write,
        )
        call = CallRecord(
            role=role,
            node_id=node_id,
            status=status,  # type: ignore[arg-type]
            requested_model=self.model,
            served_model=served,
            effort=self.effort,
            stop_reason=stop,
            refusal_category=category,
            latency_ms=self.latency_ms,
            attempts=1,
            cost_usd=cost_usd(self.model, config=self.config, **tok),
            **tok,
        )
        await asyncio.sleep(0)  # yield, so concurrency in generators is exercised
        return text, call


def params_digest(params: dict[str, Any]) -> str:
    """Stable hash of a request body (judge rows record the input hash)."""
    return hashlib.sha256(json.dumps(params, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
