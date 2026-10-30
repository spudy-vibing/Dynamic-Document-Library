"""Content model (FR-CNT-1..3, cut down). No references to Prompt/Document (FR-CNT-5)."""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Union


@dataclass(frozen=True)
class MarkdownBlock:
    text: str
    source_prompt_id: str | None = None
    edited: bool = False
    type: str = "markdown"


@dataclass(frozen=True)
class TableBlock:
    columns: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    caption: str | None = None
    type: str = "table"


@dataclass(frozen=True)
class MetricBlock:
    label: str
    value: str
    delta: str | None = None
    unit: str | None = None
    type: str = "metric"


@dataclass(frozen=True)
class ErrorBlock:
    message: str
    prompt_id: str | None = None
    type: str = "error"


Block = Union[MarkdownBlock, TableBlock, MetricBlock, ErrorBlock]


@dataclass(frozen=True)
class SectionContent:
    id: str
    title: str
    level: int
    blocks: tuple[Block, ...] = ()
    subsections: tuple["SectionContent", ...] = ()

    @property
    def markdown(self) -> str:
        from .render import blocks_to_markdown

        return blocks_to_markdown(self.blocks)


@dataclass(frozen=True)
class PromptRecord:
    id: str
    kind: str
    status: str
    provider: str | None = None
    resolved_instruction: str | None = None
    system: str | None = None
    prefix: tuple[tuple[str, str], ...] = ()
    raw_output: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    started_at: float = 0.0
    ended_at: float = 0.0
    warnings: tuple[str, ...] = ()
    error: str | None = None


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    prompts: tuple[PromptRecord, ...]
    wall_ms: float
    input_tokens: int
    output_tokens: int

    def find(self, node_id: str) -> PromptRecord:
        return next(p for p in self.prompts if p.id == node_id)


@dataclass(frozen=True)
class DocumentContent:
    title: str
    metadata: dict = field(default_factory=dict)
    preamble: tuple[Block, ...] = ()
    sections: tuple[SectionContent, ...] = ()
    run: RunRecord | None = None
    ok: bool = True
    schema_version: int = 1
    link_hosts: tuple[str, ...] = ()

    def walk(self):
        def rec(secs):
            for s in secs:
                yield s
                yield from rec(s.subsections)

        yield from rec(self.sections)

    def find(self, section_id: str) -> SectionContent:
        for s in self.walk():
            if s.id == section_id:
                return s
        raise KeyError(section_id)

    def to_dict(self, *, include_provenance: bool = False) -> dict:
        d = dataclasses.asdict(self)
        if d["run"] and not include_provenance:  # FR-CNT-8 stripping
            for p in d["run"]["prompts"]:
                for k in ("resolved_instruction", "system", "prefix", "raw_output"):
                    p[k] = None
        return d

    def to_json(self, indent: int = 2, *, include_provenance: bool = False) -> str:
        return json.dumps(self.to_dict(include_provenance=include_provenance), indent=indent,
                          ensure_ascii=False)

    def render(self, format: str, *, template=None, **options):
        from .render import render

        return render(self, format, template=template, **options)

    def save(self, path, *, format: str | None = None, template=None, **options) -> Path:
        from .render import save

        return save(self, path, format=format, template=template, **options)
