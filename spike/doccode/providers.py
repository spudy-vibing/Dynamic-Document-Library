"""Provider seam (FR-PRV-1, cut down) and MockProvider (FR-PRV-4)."""

from __future__ import annotations

import asyncio
import difflib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from .errors import ConfigError


@dataclass(frozen=True)
class CompletionRequest:
    model: str | None
    system: str
    prefix: tuple[tuple[str, str], ...]  # (name, text) shared blocks, FR-PRV-7 order
    messages: tuple[dict, ...]           # per-prompt part: one user message
    effort: str | None = None
    max_tokens: int | None = None
    response_format: dict | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def user_text(self) -> str:
        return self.messages[-1]["content"]


@dataclass(frozen=True)
class Completion:
    text: str
    input_tokens: int
    output_tokens: int
    stop_reason: str = "end_turn"
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


class MockProvider:
    """Deterministic, offline. Accepts a dict, a path to a JSON dict, or a callable(request)->str.

    Spike extra (not in the BRD): ``delays={"id": seconds}`` so tests can prove ready-queue
    scheduling (FR-EXE-3) without a real provider.
    """

    name = "mock"

    def __init__(self, responses: dict | str | Path | Callable | None = None, *,
                 delays: dict[str, float] | None = None):
        self._fn: Callable | None = None
        self.responses: dict[str, str] = {}
        if callable(responses):
            self._fn = responses
        elif isinstance(responses, (str, Path)):
            self.responses = json.loads(Path(responses).read_text(encoding="utf-8"))
            self.source = str(responses)
        elif responses:
            self.responses = dict(responses)
        self.delays = delays or {}
        self.calls: list[CompletionRequest] = []

    def fingerprint(self) -> str:
        return "mock"

    # Spike hook for the FR-PRV-4 "unknown key" rule; the real contract needs a named hook.
    def validate_ids(self, known: set[str], *, prefix: str | None = None,
                     pending_prefixes: tuple[str, ...] = ()) -> None:
        if self._fn:
            return
        keys = [k for k in self.responses
                if (k.startswith(prefix + "/") if prefix else
                    not any(k.startswith(p + "/") for p in pending_prefixes))]
        unknown = [k for k in keys if k not in known]
        if unknown:
            lines = []
            for k in unknown:
                close = difflib.get_close_matches(k, sorted(known), n=2)
                lines.append(f"{k!r}" + (f" (did you mean {', '.join(map(repr, close))}?)"
                                         if close else ""))
            raise ConfigError(
                "Mock response map has keys that match no prompt: " + "; ".join(lines),
                ids=tuple(unknown),
                hint="prompt IDs are printed by doc.plan(); give prompts an explicit id= "
                     "so renames do not break the map")

    async def complete(self, request: CompletionRequest) -> Completion:
        self.calls.append(request)
        node_id = request.metadata.get("node_id")
        kind = request.metadata.get("kind", "prompt")
        if node_id in self.delays:
            await asyncio.sleep(self.delays[node_id])
        if self._fn:
            text = self._fn(request)
        elif node_id in self.responses:
            text = self.responses[node_id]
        elif kind == "brief":
            text = "## Core message\n\n## Section points\n\n## Terms\n\n## Avoid\n"
        elif kind == "editor":
            text = '{"edits": [], "notes": []}'
        else:
            instr = request.metadata.get("instruction", request.user_text)
            text = f"[mock:{node_id}] {instr[:80]}"
        return Completion(text, len(request.user_text) // 4, len(text) // 4)


class NoProvider:
    """Stand-in for AnthropicProvider: the spike makes no live calls by default."""

    name = "none"

    def fingerprint(self) -> str:
        return "none"

    async def complete(self, request):
        raise ConfigError(
            "No provider configured (the spike has no default live provider).",
            ids=(request.metadata.get("node_id"),),
            hint="run `python -m doccode run <file> --dry-run`, or "
                 "doccode.configure(provider=MockProvider(...)); for the owner's live call, "
                 "see spike/README.md (doccode.live.AnthropicProvider)")


def default_mock_for(script: str | Path) -> MockProvider:
    p = Path(script).with_suffix(".mock.json")
    return MockProvider(p) if p.exists() else MockProvider()

