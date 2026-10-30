"""Minimal live adapter for the OWNER's one live call (WP-002). UNTESTED by the spike builder.

The builder holds no API key and made no live call. Request shape follows decision 0005
(system parameter, shared blocks as leading content blocks of the user message, effort as
output_config.effort, no temperature). No retries, no prompt-cache breakpoints, no refusals
handling. Requires `pip install anthropic` (the `live` extra in spike/pyproject.toml).

    from doccode import configure
    from doccode.live import AnthropicProvider
    configure(provider=AnthropicProvider())        # reads ANTHROPIC_API_KEY
"""

from __future__ import annotations

import os

from .providers import Completion, CompletionRequest

DEFAULT_MODEL = "claude-sonnet-5-5"  # decision 0005 (proposed)


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, *, api_key: str | None = None, model: str | None = None):
        self._api_key = api_key
        self._model = model or os.environ.get("DOCCODE_MODEL") or DEFAULT_MODEL
        self._client = None

    def fingerprint(self) -> str:
        return "anthropic:default"

    async def complete(self, request: CompletionRequest) -> Completion:
        import anthropic

        if self._client is None:
            self._client = anthropic.AsyncAnthropic(api_key=self._api_key)
        content = [{"type": "text", "text": text} for _name, text in request.prefix]
        content.append({"type": "text", "text": request.user_text})
        kwargs = dict(model=request.model or self._model, max_tokens=request.max_tokens or 4096,
                      system=request.system, messages=[{"role": "user", "content": content}])
        if request.effort:
            kwargs["output_config"] = {"effort": request.effort}
        resp = await self._client.messages.create(**kwargs)
        text = "".join(getattr(b, "text", "") for b in resp.content if b.type == "text")
        return Completion(text, resp.usage.input_tokens, resp.usage.output_tokens,
                          stop_reason=resp.stop_reason or "end_turn")
