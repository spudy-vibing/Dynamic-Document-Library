"""Shared contract: everything generators, graders, the judge and the runner agree on.

Changing a field here is a contract change: update every user and bump the
generator/grader versions that depend on it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

Origin = Literal["model", "data", "static", "error"]
CallStatus = Literal["ok", "cached", "locked", "failed", "skipped", "refusal"]


# ---------------------------------------------------------------- case spec (EVAL-1)


@dataclass(frozen=True)
class Constraints:
    max_words: int | None = None
    min_words: int | None = None
    paragraphs: int | None = None
    list_items: int | None = None
    forbidden_phrases: tuple[str, ...] = ()


@dataclass(frozen=True)
class DataBlockSpec:
    """A block rendered from data, never by a model.

    ``key`` is a dot path into data.json. A ``table`` points at a list of flat
    dicts (columns are the dict keys, in first-row order). A ``metric`` points at
    a scalar; ``label`` defaults to the key.
    """

    kind: Literal["table", "metric"]
    key: str
    label: str | None = None
    caption: str | None = None


@dataclass(frozen=True)
class CaseSection:
    id: str
    title: str
    brief: str
    instruction: str
    depends_on: tuple[str, ...] = ()
    constraints: Constraints = field(default_factory=Constraints)
    required_facts: tuple[str, ...] = ()  # keys into truth.json "values"
    data_blocks: tuple[DataBlockSpec, ...] = ()


@dataclass(frozen=True)
class AllowedDerived:
    key: str
    formula: str


@dataclass(frozen=True)
class Traps:
    derivable_not_given: tuple[str, ...] = ()  # T1: keys into truth.json "derived"
    near_collision: tuple[tuple[str, str], ...] = ()  # T2: pairs of keys into "values"
    unit: tuple[str, ...] = ()  # T3: keys into "values"
    missing: tuple[str, ...] = ()  # T4: keys whose value is null in the data
    bad_news: bool = False  # T5
    period_confusion: tuple[tuple[str, str], ...] = ()  # T6: (correct base key, wrong base key)
    injected_instruction: str | None = None  # T7: the injected text, as planted in the data
    allowed_derived: tuple[AllowedDerived, ...] = ()


@dataclass(frozen=True)
class EvalCase:
    """A library-neutral description of one document to generate.

    ``truth`` is the parsed truth.json:
        {
          "values":  {key: number | null, ...},        # every named figure in the data
          "labels":  {key: [label, ...], ...},         # how prose may name each figure (T2, T4)
          "derived": {key: {"value": number, "formula": str}, ...},  # figures computable from data
          "facts":   {key: str, ...}                   # non-numeric ground truth (e.g. licence, CLI flags)
        }
    A derived figure is a *supported* number only if its key is in ``traps.allowed_derived``.
    """

    id: str
    family: str
    spec_version: int
    title: str
    purpose: str
    audience: str
    style: str
    sections: tuple[CaseSection, ...]
    data: dict[str, Any]
    truth: dict[str, Any]
    traps: Traps
    attachments: dict[str, str]  # name -> text
    path: Path

    def section(self, section_id: str) -> CaseSection:
        for s in self.sections:
            if s.id == section_id:
                return s
        raise KeyError(section_id)


# ---------------------------------------------------------------- generation results (EVAL-2)


@dataclass
class BlockOut:
    origin: Origin
    text: str  # Markdown for model/static/error blocks; the neutral rendering for data blocks
    kind: Literal["markdown", "table", "metric"] = "markdown"
    columns: list[str] | None = None  # table
    rows: list[list[str]] | None = None  # table
    label: str | None = None  # metric
    value: str | None = None  # metric
    data_key: str | None = None  # data blocks: the DataBlockSpec.key they render


@dataclass
class SectionOut:
    id: str
    title: str
    level: int
    blocks: list[BlockOut]

    def model_text(self) -> str:
        return "\n\n".join(b.text for b in self.blocks if b.origin == "model")


@dataclass
class CallRecord:
    role: str  # "generate" | "brief" | "editor" | "judge" | "claims" | ...
    node_id: str | None
    status: CallStatus
    requested_model: str
    served_model: str | None
    effort: str
    stop_reason: str | None
    refusal_category: str | None
    input_tokens: int
    output_tokens: int  # includes thinking tokens
    cache_read_tokens: int
    cache_write_tokens: int
    latency_ms: int
    attempts: int
    cost_usd: float | None  # None for an unpriced model, never 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class GenerationResult:
    generator: str
    generator_version: str
    case_id: str
    rep: int
    title: str
    sections: list[SectionOut]
    calls: list[CallRecord]
    wall_ms: int
    markdown: str = ""  # filled by core.render.render_markdown; generators may leave it empty
    link_hosts: list[str] | None = None  # None for baselines
    warnings: list[str] = field(default_factory=list)
    flagged_numbers: list[tuple[str, str]] = field(default_factory=list)  # (section_id, raw text) a generator flagged
    error: str | None = None  # infra failure or refusal: never scored


# ---------------------------------------------------------------- scores


@dataclass
class Score:
    """One metric for one document.

    Ratio metrics set ``num`` and ``den``; reports pool them as sum(num) / sum(den)
    across documents. Other metrics are averaged over ``value``.
    """

    metric: str
    value: float | None
    num: float | None = None
    den: float | None = None
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MetricDef:
    label: str
    better: Literal["lower", "higher"]
    fmt: str  # "pct" | "num" | "usd" | "ms"


# Every metric a grader (or the claim checker / absolute rubric) may emit. Reports show them
# in this order. Per-trap metrics are emitted as "trap.<name>" (for example
# "trap.t1_derivable") and listed separately, as are the "abs.<criterion>" means.
METRICS: dict[str, MetricDef] = {
    "unr": MetricDef("UNR", "lower", "pct"),
    "unr_silent": MetricDef("Silent UNR", "lower", "pct"),
    "docs_unsupported": MetricDef("Docs w/ unsupported", "lower", "pct"),
    "xrep": MetricDef("XREP", "lower", "num"),
    "rep_pairs": MetricDef("Rep. pairs", "lower", "pct"),
    "structure": MetricDef("Structure", "higher", "num"),
    "chatter": MetricDef("Chatter", "lower", "pct"),
    "adherence": MetricDef("Adherence", "higher", "pct"),
    "data_exact": MetricDef("Data exact", "higher", "pct"),
    "length_ratio": MetricDef("Length ratio", "lower", "num"),
    "cost_usd": MetricDef("$/doc", "lower", "usd"),
    "cache_hit": MetricDef("Cache hit", "higher", "pct"),
    "wall_ms": MetricDef("Wall", "lower", "ms"),
    # Claim checker (EVAL-11, evals/judge/claims.py), from claims.jsonl.
    "misattrib": MetricDef("Misattrib.", "lower", "pct"),
    "misattrib_silent": MetricDef("Silent misattrib.", "lower", "pct"),
    "docs_misattrib_silent": MetricDef("Docs w/ silent misattrib.", "lower", "pct"),
    # Absolute rubric floors (EVAL-10, BRD §1.5, evals/judge/absolute.py), from absolute.jsonl.
    # Per-criterion means are emitted as "abs.<criterion>" and are diagnosis only, never ranked.
    "abs_floor": MetricDef("Docs ≤ floor on any criterion", "lower", "pct"),
    "abs_c1_is_1": MetricDef("Docs with C1 accuracy = 1", "lower", "pct"),
}


# ---------------------------------------------------------------- protocols


@dataclass
class LLMResponse:
    text: str
    call: CallRecord


class RefusalError(Exception):
    """The model refused twice (EVAL-21). The slot is recorded as an error row, never scored."""

    def __init__(self, call: CallRecord) -> None:
        super().__init__(f"refusal ({call.refusal_category}) from {call.requested_model}")
        self.call = call


class InfraError(Exception):
    """Served-model mismatch, exhausted retries or another non-quality failure. Never scored."""


class LLM(Protocol):
    """Harness-owned model access for one (generator, case, rep) slot.

    Every completed call, including failed and refused ones, is appended to ``calls``.
    Implementations set effort explicitly, never send temperature/top_p/top_k,
    never use refusal fallbacks, and assert the served model (EVAL-21).
    """

    calls: list[CallRecord]

    async def complete(
        self,
        *,
        role: str,
        node_id: str | None,
        system: str,
        messages: list[dict[str, Any]],
        max_tokens: int | None = None,
    ) -> LLMResponse: ...


class Generator(Protocol):
    name: str  # "b0-oneshot", "b1-parallel", "b2-chain", "dc-report", ...
    version: str  # bump when prompts or logic change

    async def generate(self, case: EvalCase, llm: LLM, rep: int) -> GenerationResult: ...


class Grader(Protocol):
    name: str
    version: str

    def grade(self, case: EvalCase, result: GenerationResult) -> list[Score]: ...
