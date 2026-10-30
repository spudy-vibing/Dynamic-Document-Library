"""Shared test fakes: a tiny case on disk, a stubbed Anthropic client, a trivial grader.

Nothing here touches the network. Other test modules may import from this file.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from evals.core.cases import load_case
from evals.core.types import EvalCase, GenerationResult, Score

TINY_CASE_TOML = """\
id = "tiny-01"
family = "f2_quarterly"
spec_version = 1
title = "Tinyco Q3 Report"
purpose = "Quarterly update for the leadership team."
audience = "Leadership."
style = "Plain English. Short paragraphs."
data = "data.json"
truth = "truth.json"

[[sections]]
id = "summary"
title = "Executive Summary"
brief = "Headline results"
instruction = "Summarise the quarter in one paragraph."
constraints = { max_words = 80, paragraphs = 1 }
data_blocks = [{ kind = "metric", key = "metrics.revenue", label = "Revenue (USD)" }]

[[sections]]
id = "analysis"
title = "Financial Analysis"
brief = "Revenue and margin"
instruction = "Analyse revenue and margin."
depends_on = ["summary"]
constraints = { max_words = 120, forbidden_phrases = ["synergy"] }
data_blocks = [{ kind = "table", key = "kpis", caption = "Key figures" }]

[[sections]]
id = "next"
title = "Next Steps"
brief = "Three actions"
instruction = "Give three actions as a bulleted list."
depends_on = ["summary", "analysis"]
constraints = { list_items = 3 }

[traps]
missing = ["nps"]
"""

TINY_DATA = {
    "company": "Tinyco",
    "metrics": {"revenue": 4210000, "margin_pct": 23.4, "nps": None},
    "kpis": [{"kpi": "Revenue", "Q2": 3980000, "Q3": 4210000}, {"kpi": "Margin (%)", "Q2": 22.9, "Q3": 23.4}],
}
TINY_TRUTH = {
    "values": {"revenue": 4210000, "margin_pct": 23.4, "nps": None},
    "labels": {},
    "derived": {},
    "facts": {},
}


def write_tiny_case(root: Path, *, case_id: str = "tiny-01", attachment: str | None = "Team notes: shipped 7 things.") -> EvalCase:
    d = Path(root) / "dev" / "f2_quarterly" / case_id
    d.mkdir(parents=True, exist_ok=True)
    toml = TINY_CASE_TOML.replace('id = "tiny-01"', f'id = "{case_id}"')
    if attachment is not None:
        toml = toml.replace('truth = "truth.json"\n', 'truth = "truth.json"\n\n[attachments]\n"notes.md" = "notes.md"\n', 1)
        (d / "notes.md").write_text(attachment)
    (d / "case.toml").write_text(toml)
    (d / "data.json").write_text(json.dumps(TINY_DATA, indent=2))
    (d / "truth.json").write_text(json.dumps(TINY_TRUTH, indent=2))
    return load_case(d)


class WordCountGrader:
    """Trivial grader for e2e tests: model words per doc, and a pooled ratio metric."""

    name = "test_words"
    version = "1"

    def grade(self, case: EvalCase, result: GenerationResult) -> list[Score]:
        if result.error:
            return []
        words = sum(len(s.model_text().split()) for s in result.sections)
        with_text = sum(1 for s in result.sections if s.model_text().strip())
        return [
            Score("length_ratio", words / 100.0),
            Score("structure", None, num=with_text, den=len(result.sections)),
            Score("trap.t4_missing", 0.0, num=0, den=1),
        ]


# ---------------------------------------------------------------- stubbed Anthropic client


def stub_message(
    text: str = "Hello.",
    *,
    model: str = "claude-sonnet-5-5",
    stop_reason: str = "end_turn",
    category: str | None = None,
    stop_details: bool = True,
    input_tokens: int = 100,
    output_tokens: int = 50,
    cache_read: int = 0,
    cache_write: int = 0,
    thinking: str | None = "secret reasoning",
) -> SimpleNamespace:
    content = []
    if thinking is not None:
        content.append(SimpleNamespace(type="thinking", thinking=thinking))
    if text:
        content.append(SimpleNamespace(type="text", text=text))
    details = SimpleNamespace(type="refusal", category=category, explanation=None) if stop_reason == "refusal" and stop_details else None
    return SimpleNamespace(
        id="msg_stub",
        type="message",
        role="assistant",
        model=model,
        content=content,
        stop_reason=stop_reason,
        stop_details=details,
        usage=SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_input_tokens=cache_read,
            cache_creation_input_tokens=cache_write,
        ),
    )


class _Stream:
    def __init__(self, outcome: Any) -> None:
        self.outcome = outcome

    async def __aenter__(self) -> "_Stream":
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def get_final_message(self) -> Any:
        return self.outcome


class _AsyncIter:
    def __init__(self, items: list[Any]) -> None:
        self.items = list(items)

    def __aiter__(self) -> "_AsyncIter":
        return self

    async def __anext__(self) -> Any:
        if not self.items:
            raise StopAsyncIteration
        return self.items.pop(0)


class StubBatches:
    """``messages.batches`` stub. ``responder(custom_id, params) -> message | None`` (None = errored)."""

    def __init__(self, responder: Any, polls_before_end: int = 1) -> None:
        self.responder = responder
        self.polls_before_end = polls_before_end
        self.created: list[list[dict[str, Any]]] = []
        self._polls: dict[str, int] = {}

    async def create(self, *, requests: list[dict[str, Any]], **kw: Any) -> Any:
        assert not kw, f"unexpected batch kwargs {kw}"
        bid = f"msgbatch_{len(self.created) + 1}"
        self.created.append(list(requests))
        self._polls[bid] = 0
        return SimpleNamespace(id=bid, processing_status="in_progress")

    async def retrieve(self, batch_id: str, **kw: Any) -> Any:
        self._polls[batch_id] += 1
        status = "ended" if self._polls[batch_id] > self.polls_before_end else "in_progress"
        return SimpleNamespace(id=batch_id, processing_status=status)

    async def results(self, batch_id: str, **kw: Any) -> _AsyncIter:
        reqs = self.created[int(batch_id.rsplit("_", 1)[1]) - 1]
        items = []
        for r in reversed(reqs):  # deliberately not in submission order
            msg = self.responder(r["custom_id"], r["params"])
            if msg is None:
                result = SimpleNamespace(type="errored", error=SimpleNamespace(type="api_error"))
            else:
                result = SimpleNamespace(type="succeeded", message=msg)
            items.append(SimpleNamespace(custom_id=r["custom_id"], result=result))
        return _AsyncIter(items)


class StubMessages:
    def __init__(self, outcomes: list[Any], batches: StubBatches | None = None) -> None:
        self.outcomes = list(outcomes)
        self.requests: list[dict[str, Any]] = []
        self.batches = batches

    def _next(self, kwargs: dict[str, Any]) -> Any:
        self.requests.append(kwargs)
        if not self.outcomes:
            raise AssertionError("stub client: no scripted outcome left")
        return self.outcomes.pop(0)

    def stream(self, **kwargs: Any) -> _Stream:
        return _Stream(self._next(kwargs))

    async def create(self, **kwargs: Any) -> Any:
        out = self._next(kwargs)
        if isinstance(out, BaseException):
            raise out
        return out


class StubClient:
    """Stands in for ``anthropic.AsyncAnthropic``: scripted outcomes (messages or exceptions)."""

    def __init__(self, outcomes: list[Any] | None = None, batches: StubBatches | None = None) -> None:
        self.messages = StubMessages(outcomes or [], batches)


def api_error(status: int, error_type: str = "overloaded_error") -> Exception:
    """A real SDK exception for ``status`` (no network: the response object is built locally)."""
    import anthropic
    import httpx2

    resp = httpx2.Response(status, request=httpx2.Request("POST", "https://api.invalid/v1/messages"))
    body = {"type": "error", "error": {"type": error_type, "message": "stub"}}
    cls = {
        400: anthropic.BadRequestError,
        429: anthropic.RateLimitError,
        500: anthropic.InternalServerError,
        529: anthropic.OverloadedError,
    }.get(status, anthropic.APIStatusError)
    return cls("stub error", response=resp, body=body)
