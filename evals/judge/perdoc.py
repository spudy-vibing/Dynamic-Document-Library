"""Per-document judging engine shared by the absolute rubric and the claim checker (WP-015).

Each ``DocJob`` is one model call about one document (one criterion group of the absolute
rubric, or one claim check). Rows go to ``<stream>.jsonl`` (``absolute`` or ``claims``),
one per finished job, and are resumable exactly like ``judge.jsonl``: a job whose
``custom_id`` already has an ``ok`` row with the current version is skipped; errored jobs
are retried on the next invocation.

The rules are the pairwise judge's (EVAL-21): every call goes through the ``LLM`` wrapper
or ``build_params`` (explicit effort, no sampling parameters, no fallbacks), the served
model is asserted, and a refusal is retried once, then recorded as an error row and never
scored. Parse failures are ``parse_error`` rows, also never scored.
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Iterable

from evals.core.llm import build_params, params_digest, record_from_message, response_text
from evals.core.storage import RunStore, now_iso
from evals.core.types import LLM, EvalCase, GenerationResult, InfraError, RefusalError

BATCH_COST_FACTOR = 0.5  # Message Batches bill at 50% of standard prices

Parser = Callable[[str], dict[str, Any]]  # reply text -> fields for an ok row; raises ValueError


def doc_key(generator: str, case_id: str, rep: int) -> str:
    return hashlib.sha1(f"{generator}|{case_id}|{rep}".encode()).hexdigest()[:20]


@dataclass
class DocJob:
    """One call about one document. ``custom_id`` matches Batches' ``[a-zA-Z0-9_-]{1,64}``."""

    custom_id: str
    case: EvalCase
    result: GenerationResult
    system: str
    messages: list[dict[str, Any]]
    parse: Parser
    fields: dict[str, Any] = field(default_factory=dict)  # copied into every row (e.g. the group)

    @property
    def input_hash(self) -> str:
        return params_digest({"system": self.system, "messages": self.messages})


def done_ids(rows: Iterable[dict[str, Any]], version: str) -> set[str]:
    return {r["custom_id"] for r in rows if r.get("status") == "ok" and r.get("version") == version}


def latest_ok(rows: Iterable[dict[str, Any]], version: str) -> dict[str, dict[str, Any]]:
    """custom_id -> the last ok row with ``version`` (a retried job may also have error rows)."""
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        if r.get("status") == "ok" and r.get("version") == version:
            out[r["custom_id"]] = r
    return out


def unresolved_errors(rows: Iterable[dict[str, Any]], version: str) -> int:
    """Jobs whose latest attempt with ``version`` failed (refusal, infra, parse error)."""
    last: dict[str, str] = {}
    for r in rows:
        if r.get("version") == version:
            last[r["custom_id"]] = r.get("status", "")
    return sum(1 for s in last.values() if s != "ok")


def make_row(job: DocJob, version: str, **fields: Any) -> dict[str, Any]:
    r = job.result
    return {
        "custom_id": job.custom_id,
        "generator": r.generator,
        "generator_version": r.generator_version,
        "case_id": r.case_id,
        "rep": r.rep,
        **job.fields,
        "version": version,
        "input_hash": job.input_hash,
        "ts": now_iso(),
        **fields,
    }


def record(store: RunStore, stream: str, row: dict[str, Any], calls: list[Any]) -> None:
    """Append the row, its CallRecords (phase = stream) and any error rows."""
    slot = {"custom_id": row["custom_id"], "generator": row["generator"], "case_id": row["case_id"], "rep": row["rep"]}
    store.append(stream, [row])
    store.append("calls", [{"phase": stream, **slot, **c.to_dict()} for c in calls])
    errors = [{"phase": stream, "kind": "refusal", **slot, "category": c.refusal_category, "ts": now_iso()}
              for c in calls if c.status == "refusal"]
    if row["status"] != "ok":
        errors.append({"phase": stream, "kind": row["status"], **slot, "message": row.get("error"), "ts": now_iso()})
    store.append("errors", errors)


def finish(job: DocJob, version: str, text: str, **extra: Any) -> dict[str, Any]:
    try:
        return make_row(job, version, status="ok", **job.parse(text), raw=text, **extra)
    except ValueError as e:
        return make_row(job, version, status="parse_error", error=str(e), raw=text, **extra)


async def run_with_llm(
    jobs: list[DocJob],
    make_llm: Callable[[], LLM],
    store: RunStore,
    stream: str,
    version: str,
    *,
    role: str,
    concurrency: int = 4,
    max_tokens: int | None = None,
) -> None:
    """One ``LLM.complete`` per job (fake mode, ``--cli``, or live without batches)."""
    sem = asyncio.Semaphore(concurrency)

    async def one(job: DocJob) -> None:
        llm = make_llm()
        async with sem:
            try:
                resp = await llm.complete(role=role, node_id=job.custom_id, system=job.system,
                                          messages=job.messages, max_tokens=max_tokens)
            except (RefusalError, InfraError) as e:
                kind = "refusal" if isinstance(e, RefusalError) else "infra"
                record(store, stream, make_row(job, version, status=kind, error=str(e), raw=None), llm.calls)
                return
        record(store, stream, finish(job, version, resp.text), llm.calls)

    await asyncio.gather(*(one(j) for j in jobs))


async def run_with_batches(
    jobs: list[DocJob],
    client: Any,
    config: dict[str, Any],
    store: RunStore,
    stream: str,
    version: str,
    *,
    model: str,
    effort: str,
    max_tokens: int,
    role: str,
    poll_s: float = 30.0,
    max_rounds: int = 3,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    log: Callable[[str], None] = lambda s: None,
) -> None:
    """Message Batches API (live only), with the pairwise judge's semantics.

    Results are mapped by custom_id, never by position. A refused item is resubmitted once;
    errored, expired or canceled items are resubmitted up to ``max_rounds``. Usage cost gets
    the 50% Batches factor. Submitted batch IDs are kept in ``<stream>_batches.jsonl`` so an
    interrupted run collects them on restart instead of paying twice.
    """
    by_id = {j.custom_id: j for j in jobs}
    refusals: dict[str, list[Any]] = {}
    attempts: dict[str, int] = {}
    finished: set[str] = set()
    state_name = f"{stream}_batches"

    def params(job: DocJob) -> dict[str, Any]:
        return build_params(model=model, effort=effort, max_tokens=max_tokens, system=job.system, messages=job.messages)

    async def collect(batch_id: str, ids: list[str]) -> None:
        while True:
            b = await client.messages.batches.retrieve(batch_id)
            if b.processing_status == "ended":
                break
            log(f"{stream} batch {batch_id}: {b.processing_status}")
            await sleep(poll_s)
        results = {}
        async for item in await client.messages.batches.results(batch_id):
            results[item.custom_id] = item
        for cid in ids:
            job = by_id.get(cid)
            if job is None or cid in finished:
                continue
            attempts[cid] = attempts.get(cid, 0) + 1
            item = results.get(cid)
            if item is None or item.result.type != "succeeded":
                continue  # resubmitted next round
            msg = item.result.message
            call = record_from_message(msg, role=role, node_id=cid, requested_model=model, effort=effort,
                                       latency_ms=0, attempts=attempts[cid], config=config,
                                       cost_factor=BATCH_COST_FACTOR)
            if call.status == "failed":
                finished.add(cid)
                record(store, stream, make_row(job, version, status="infra", batch=batch_id, raw=None,
                                               error=f"served model {call.served_model} != {model}"),
                       refusals.pop(cid, []) + [call])
            elif call.status == "refusal":
                refusals.setdefault(cid, []).append(call)
                if len(refusals[cid]) >= 2:
                    finished.add(cid)
                    record(store, stream, make_row(job, version, status="refusal", batch=batch_id, raw=None,
                                                   error=f"refused twice ({call.refusal_category})"),
                           refusals.pop(cid))
            else:
                finished.add(cid)
                record(store, stream, finish(job, version, response_text(msg), batch=batch_id),
                       refusals.pop(cid, []) + [call])
        store.append(state_name, [{"batch_id": batch_id, "event": "collected", "ts": now_iso()}])

    state = store.read(state_name)
    collected = {r["batch_id"] for r in state if r["event"] == "collected"}
    for r in state:
        if r["event"] == "submitted" and r["batch_id"] not in collected:
            log(f"collecting earlier {stream} batch {r['batch_id']}")
            await collect(r["batch_id"], r["custom_ids"])

    for _ in range(max_rounds):
        todo = [j for j in jobs if j.custom_id not in finished]
        if not todo:
            break
        batch = await client.messages.batches.create(
            requests=[{"custom_id": j.custom_id, "params": params(j)} for j in todo]
        )
        store.append(state_name, [{"batch_id": batch.id, "event": "submitted",
                                   "custom_ids": [j.custom_id for j in todo], "ts": now_iso()}])
        log(f"submitted {stream} batch {batch.id} with {len(todo)} requests")
        await collect(batch.id, [j.custom_id for j in todo])

    for j in jobs:
        if j.custom_id not in finished:
            record(store, stream, make_row(j, version, status="infra", raw=None,
                                           error=f"no successful batch result after {max_rounds} rounds"),
                   refusals.pop(j.custom_id, []))
