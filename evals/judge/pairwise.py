"""Pairwise judge (EVAL-9, EVAL-12, EVALS_PROPOSAL §6.2).

For each case and rep, the documents of two generators with the same rep index are
compared in both orders. A win counts only when both orders pick the same document
(labels mapped back); otherwise the question is a tie. The judge sees only anonymised,
normalised Markdown under the labels A and B.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import random
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable

from evals.core.llm import (
    FakeLLM,
    build_params,
    params_digest,
    record_from_message,
    response_text,
)
from evals.core.render import render_markdown
from evals.core.storage import RunStore, now_iso
from evals.core.types import LLM, EvalCase, GenerationResult, InfraError, RefusalError
from evals.generators.common import load_prompt

RUBRICS_DIR = Path(__file__).resolve().parent / "rubrics"
RUBRIC = "pairwise"
QUESTIONS = ("q1_coherence", "q2_redundancy", "q3_purpose", "overall")
QUESTION_LABELS = {
    "q1_coherence": "Q1 coherence",
    "q2_redundancy": "Q2 less redundancy",
    "q3_purpose": "Q3 purpose & audience",
    "overall": "Overall",
}
VERDICTS = ("A", "B", "tie")
BATCH_COST_FACTOR = 0.5  # Message Batches bill at 50% of standard prices
LENGTH_BIAS_FLAG = 0.3
BOOTSTRAP_RESAMPLES = 10_000
BOOTSTRAP_SEED = 20261029


def rubric():
    return load_prompt(RUBRIC, RUBRICS_DIR)


def judge_version(config: dict[str, Any]) -> str:
    """config judge.version + the rubric file's hash (EVAL-12)."""
    return f"{config['judge']['version']}-{rubric().sha[:8]}"


def word_count(markdown: str) -> int:
    return len(re.findall(r"\b\w[\w'’.-]*\b", markdown))


def normalised_markdown(result: GenerationResult) -> str:
    """The only thing the judge sees of a document: neutral Markdown, no generator metadata."""
    return render_markdown(result.title, result.sections)


# ---------------------------------------------------------------- pairing


@dataclass
class PairTask:
    case_id: str
    rep: int
    x: str  # the pair as configured: win rates are reported for x against y
    y: str
    first: str  # the generator shown as A in order 1 (random, seeded)
    docs: dict[str, str]
    words: dict[str, int]

    @property
    def key(self) -> str:
        return hashlib.sha1(f"{self.case_id}|{self.rep}|{self.x}|{self.y}".encode()).hexdigest()[:20]

    def custom_id(self, order: int) -> str:
        return f"j{self.key}-o{order}"  # Batches custom_id: [a-zA-Z0-9_-]{1,64}

    def labels(self, order: int) -> tuple[str, str]:
        second = self.y if self.first == self.x else self.x
        return (self.first, second) if order == 1 else (second, self.first)


def plan_pairs(
    results: Iterable[GenerationResult], pairs: Iterable[Iterable[str]], seed: str
) -> tuple[list[PairTask], list[dict[str, Any]]]:
    """Pair documents of two generators for the same case and rep; skip slots with errors.

    Returns the tasks and one "skipped" note per pair slot that could not be judged.
    """
    by_slot = {(r.generator, r.case_id, r.rep): r for r in results}
    slots = sorted({(c, rep) for _, c, rep in by_slot})
    tasks, skipped = [], []
    for x, y in (tuple(p) for p in pairs):
        for case_id, rep in slots:
            rx, ry = by_slot.get((x, case_id, rep)), by_slot.get((y, case_id, rep))
            if rx is None and ry is None:
                continue
            if rx is None or ry is None or rx.error or ry.error:
                skipped.append({"case_id": case_id, "rep": rep, "pair": [x, y], "reason": "missing or errored document"})
                continue
            rng = random.Random(f"{seed}|{case_id}|{rep}|{x}|{y}")
            docs = {x: normalised_markdown(rx), y: normalised_markdown(ry)}
            tasks.append(
                PairTask(case_id, rep, x, y, first=rng.choice([x, y]), docs=docs, words={g: word_count(d) for g, d in docs.items()})
            )
    return tasks, skipped


# ---------------------------------------------------------------- prompt and parsing


def outline_for_judge(case: EvalCase) -> str:
    lines = []
    for i, s in enumerate(case.sections, 1):
        lines.append(f"{i}. {s.title}: {s.brief}. Instruction: {s.instruction}")
    return "\n".join(lines)


def build_request(case: EvalCase, doc_a: str, doc_b: str) -> tuple[str, list[dict[str, Any]]]:
    """System = rubric; user = [case context (cached, identical for every pair of a case), documents]."""
    r = rubric()
    context = r.render(
        "user",
        title=case.title,
        purpose=case.purpose,
        audience=case.audience or "-",
        style=case.style or "-",
        outline=outline_for_judge(case),
        data_json=json.dumps(case.data, indent=2, ensure_ascii=False),
    )
    docs = r.render("documents", doc_a=doc_a.strip(), doc_b=doc_b.strip())
    content = [
        {"type": "text", "text": context, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": docs},
    ]
    return r.render("system"), [{"role": "user", "content": content}]


def parse_verdicts(text: str) -> tuple[dict[str, str], dict[str, list[str]]]:
    """Extract {question: "A"|"B"|"tie"} (and evidence) from the judge's JSON reply."""
    candidates = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])
    for raw in candidates:
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        verdicts, evidence = {}, {}
        for q in QUESTIONS:
            item = obj.get(q)
            v = item.get("verdict") if isinstance(item, dict) else item
            v = {"a": "A", "b": "B", "tie": "tie"}.get(str(v).strip().lower()) if v is not None else None
            if v is None:
                break
            verdicts[q] = v
            evidence[q] = list(item.get("evidence", [])) if isinstance(item, dict) else []
        else:
            return verdicts, evidence
    raise ValueError("no valid verdict JSON in judge reply")


# ---------------------------------------------------------------- combining orders


@dataclass
class PairOutcome:
    case_id: str
    rep: int
    x: str
    y: str
    winners: dict[str, str | None]  # question -> winning generator, None for a tie
    words: dict[str, int] = field(default_factory=dict)

    def score_x(self, question: str) -> float:
        w = self.winners[question]
        return 0.5 if w is None else (1.0 if w == self.x else 0.0)


def combine(rows: Iterable[dict[str, Any]]) -> list[PairOutcome]:
    """Both orders must agree for a win; disagreement (or a tie in either order) is a tie."""
    groups: dict[tuple[str, int, str, str], dict[int, dict[str, Any]]] = {}
    for row in rows:
        if row.get("status") != "ok":
            continue
        k = (row["case_id"], row["rep"], row["pair"][0], row["pair"][1])
        groups.setdefault(k, {})[row["order"]] = row
    outcomes = []
    for (case_id, rep, x, y), orders in sorted(groups.items()):
        if set(orders) != {1, 2}:
            continue
        winners: dict[str, str | None] = {}
        for q in QUESTIONS:
            picks = []
            for row in orders.values():
                v = row["verdicts"][q]
                picks.append(row["label_a"] if v == "A" else row["label_b"] if v == "B" else None)
            winners[q] = picks[0] if picks[0] is not None and picks[0] == picks[1] else None
        outcomes.append(PairOutcome(case_id, rep, x, y, winners, dict(orders[1].get("words", {}))))
    return outcomes


# ---------------------------------------------------------------- statistics


def ratio_bootstrap_ci(
    clusters: list[tuple[float, float]],
    *,
    resamples: int = BOOTSTRAP_RESAMPLES,
    seed: int = BOOTSTRAP_SEED,
    alpha: float = 0.05,
) -> tuple[float, float] | None:
    """Percentile CI of sum(num)/sum(den), resampling clusters (cases) with replacement."""
    if not clusters:
        return None
    rng = random.Random(seed)
    k = len(clusters)
    stats = []
    for _ in range(resamples):
        num = den = 0.0
        for _ in range(k):
            a, b = clusters[rng.randrange(k)]
            num += a
            den += b
        if den:
            stats.append(num / den)
    if not stats:
        return None
    stats.sort()
    lo = stats[int(math.floor(alpha / 2 * (len(stats) - 1)))]
    hi = stats[int(math.ceil((1 - alpha / 2) * (len(stats) - 1)))]
    return lo, hi


def win_rate(outcomes: list[PairOutcome], question: str, **bootstrap: Any) -> dict[str, Any]:
    """Win rate of x against y: (wins + 0.5 ties) / pairs, with a cluster-bootstrap CI over cases."""
    per_case: dict[str, list[float]] = {}
    wins = ties = losses = 0
    for o in outcomes:
        s = o.score_x(question)
        per_case.setdefault(o.case_id, []).append(s)
        wins += s == 1.0
        ties += s == 0.5
        losses += s == 0.0
    n = wins + ties + losses
    clusters = [(sum(v), float(len(v))) for _, v in sorted(per_case.items())]
    ci = ratio_bootstrap_ci(clusters, **bootstrap)
    return {
        "win_rate": (wins + 0.5 * ties) / n if n else None,
        "ci": list(ci) if ci else None,
        "wins": wins,
        "ties": ties,
        "losses": losses,
        "pairs": n,
        "cases": len(per_case),
    }


def pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((a - mx) ** 2 for a in xs)
    syy = sum((b - my) ** 2 for b in ys)
    if sxx == 0 or syy == 0:
        return None
    return sum((a - mx) * (b - my) for a, b in zip(xs, ys)) / math.sqrt(sxx * syy)


def length_bias(outcomes: list[PairOutcome], question: str = "overall") -> dict[str, Any]:
    """Correlation between x's score and its word-count advantage; |r| > 0.3 is flagged."""
    pts = [(o.score_x(question), o.words.get(o.x, 0) - o.words.get(o.y, 0)) for o in outcomes if o.words]
    r = pearson([p[0] for p in pts], [float(p[1]) for p in pts])
    return {"r": r, "n": len(pts), "flag": r is not None and abs(r) > LENGTH_BIAS_FLAG}


def summarise(rows: list[dict[str, Any]], pairs: Iterable[Iterable[str]], **bootstrap: Any) -> list[dict[str, Any]]:
    outcomes = combine(rows)
    out = []
    for x, y in (tuple(p) for p in pairs):
        sel = [o for o in outcomes if o.x == x and o.y == y]
        out.append(
            {
                "x": x,
                "y": y,
                "questions": {q: win_rate(sel, q, **bootstrap) for q in QUESTIONS},
                "length_bias": length_bias(sel),
            }
        )
    return out


# ---------------------------------------------------------------- fake judge


def fake_judge_responder(role: str, node_id: str | None, system: str, messages: list[dict[str, Any]]) -> str:
    """Deterministic stand-in: prefers the longer document (by >5%), and the shorter one on redundancy."""
    text = "\n".join(b.get("text", "") for b in messages[-1]["content"] if isinstance(b, dict))
    a = re.search(r"<document_a>\n?(.*?)</document_a>", text, re.S)
    b = re.search(r"<document_b>\n?(.*?)</document_b>", text, re.S)
    wa, wb = word_count(a.group(1) if a else ""), word_count(b.group(1) if b else "")
    longer = "A" if wa > wb * 1.05 else "B" if wb > wa * 1.05 else "tie"
    shorter = {"A": "B", "B": "A", "tie": "tie"}[longer]
    ev = [f"A: {wa} words", f"B: {wb} words"]
    verdicts = {"q1_coherence": longer, "q2_redundancy": shorter, "q3_purpose": longer, "overall": longer}
    return json.dumps({q: {"evidence": ev, "verdict": v} for q, v in verdicts.items()})


# ---------------------------------------------------------------- running


@dataclass
class JudgeJob:
    task: PairTask
    order: int
    case: EvalCase

    @property
    def custom_id(self) -> str:
        return self.task.custom_id(self.order)

    def request(self) -> tuple[str, list[dict[str, Any]]]:
        a, b = self.task.labels(self.order)
        return build_request(self.case, self.task.docs[a], self.task.docs[b])


def order_row(job: JudgeJob, jv: str, input_hash: str, **fields: Any) -> dict[str, Any]:
    a, b = job.task.labels(job.order)
    return {
        "custom_id": job.custom_id,
        "case_id": job.task.case_id,
        "rep": job.task.rep,
        "pair": [job.task.x, job.task.y],
        "order": job.order,
        "label_a": a,
        "label_b": b,
        "words": job.task.words,
        "judge_version": jv,
        "input_hash": input_hash,
        "ts": now_iso(),
        **fields,
    }


def pending_jobs(tasks: list[PairTask], cases: dict[str, EvalCase], done_ids: set[str]) -> list[JudgeJob]:
    return [
        JudgeJob(t, order, cases[t.case_id])
        for t in tasks
        for order in (1, 2)
        if t.custom_id(order) not in done_ids
    ]


def _record(store: RunStore, row: dict[str, Any], calls: list[Any]) -> None:
    store.append("judge", [row])
    store.append(
        "calls",
        [{"phase": "judge", "custom_id": row["custom_id"], "case_id": row["case_id"], "rep": row["rep"],
          "pair": row["pair"], **c.to_dict()} for c in calls],
    )
    for c in calls:
        if c.status == "refusal":
            store.append("errors", [{"phase": "judge", "kind": "refusal", "custom_id": row["custom_id"],
                                     "case_id": row["case_id"], "rep": row["rep"], "pair": row["pair"],
                                     "category": c.refusal_category, "ts": now_iso()}])
    if row["status"] != "ok":
        store.append("errors", [{"phase": "judge", "kind": row["status"], "custom_id": row["custom_id"],
                                 "case_id": row["case_id"], "rep": row["rep"], "pair": row["pair"],
                                 "message": row.get("error"), "ts": now_iso()}])


async def judge_with_llm(
    jobs: list[JudgeJob],
    make_llm: Callable[[], LLM],
    store: RunStore,
    jv: str,
    *,
    concurrency: int = 4,
    max_tokens: int | None = None,
) -> None:
    """One ``LLM.complete`` per job (fake mode, or live without batches)."""
    sem = asyncio.Semaphore(concurrency)

    async def one(job: JudgeJob) -> None:
        system, messages = job.request()
        llm = make_llm()
        ih = params_digest({"system": system, "messages": messages})
        async with sem:
            try:
                resp = await llm.complete(role="judge", node_id=job.custom_id, system=system, messages=messages,
                                          max_tokens=max_tokens)
            except (RefusalError, InfraError) as e:
                kind = "refusal" if isinstance(e, RefusalError) else "infra"
                _record(store, order_row(job, jv, ih, status=kind, error=str(e), verdicts=None, raw=None), llm.calls)
                return
        try:
            verdicts, evidence = parse_verdicts(resp.text)
            row = order_row(job, jv, ih, status="ok", verdicts=verdicts, evidence=evidence, raw=resp.text)
        except ValueError as e:
            row = order_row(job, jv, ih, status="parse_error", error=str(e), verdicts=None, raw=resp.text)
        _record(store, row, llm.calls)

    await asyncio.gather(*(one(j) for j in jobs))


def make_fake_judge(config: dict[str, Any]) -> Callable[[], LLM]:
    model, effort = config["models"]["judge"], config["effort"]["judge"]
    return lambda: FakeLLM(model, effort, config["max_tokens"]["judge"], config, responder=fake_judge_responder)


async def judge_with_batches(
    jobs: list[JudgeJob],
    client: Any,
    config: dict[str, Any],
    store: RunStore,
    jv: str,
    *,
    poll_s: float = 30.0,
    max_rounds: int = 3,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    log: Callable[[str], None] = lambda s: None,
) -> None:
    """Message Batches API (live only). Results are mapped by custom_id, never by position.

    A refused item is resubmitted once in the next round; errored, expired or canceled
    items are resubmitted up to ``max_rounds``. Usage cost gets the 50% Batches factor.
    Submitted batch IDs are kept in ``judge_batches.jsonl`` so an interrupted run
    collects them on restart instead of paying twice.
    """
    model, effort = config["models"]["judge"], config["effort"]["judge"]
    max_tokens = config["max_tokens"]["judge"]
    by_id = {j.custom_id: j for j in jobs}
    refusals: dict[str, list[Any]] = {}
    attempts: dict[str, int] = {}
    finished: set[str] = set()

    def params(job: JudgeJob) -> dict[str, Any]:
        system, messages = job.request()
        return build_params(model=model, effort=effort, max_tokens=max_tokens, system=system, messages=messages)

    async def collect(batch_id: str, ids: list[str]) -> None:
        while True:
            b = await client.messages.batches.retrieve(batch_id)
            if b.processing_status == "ended":
                break
            log(f"batch {batch_id}: {b.processing_status}")
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
            p = params(job)
            ih = params_digest({"system": p.get("system"), "messages": p["messages"]})
            msg = item.result.message
            call = record_from_message(msg, role="judge", node_id=cid, requested_model=model, effort=effort,
                                       latency_ms=0, attempts=attempts[cid], config=config,
                                       cost_factor=BATCH_COST_FACTOR)
            if call.status == "failed":
                finished.add(cid)
                _record(store, order_row(job, jv, ih, status="infra", batch=batch_id, verdicts=None, raw=None,
                                         error=f"served model {call.served_model} != {model}"),
                        refusals.pop(cid, []) + [call])
            elif call.status == "refusal":
                refusals.setdefault(cid, []).append(call)
                if len(refusals[cid]) >= 2:
                    finished.add(cid)
                    _record(store, order_row(job, jv, ih, status="refusal", batch=batch_id, verdicts=None, raw=None,
                                             error=f"refused twice ({call.refusal_category})"), refusals.pop(cid))
            else:
                finished.add(cid)
                text = response_text(msg)
                try:
                    verdicts, evidence = parse_verdicts(text)
                    row = order_row(job, jv, ih, status="ok", batch=batch_id, verdicts=verdicts, evidence=evidence, raw=text)
                except ValueError as e:
                    row = order_row(job, jv, ih, status="parse_error", batch=batch_id, error=str(e), verdicts=None, raw=text)
                _record(store, row, refusals.pop(cid, []) + [call])
        store.append("judge_batches", [{"batch_id": batch_id, "event": "collected", "ts": now_iso()}])

    # Collect batches an earlier, interrupted invocation submitted but never collected.
    state = store.read("judge_batches")
    collected = {r["batch_id"] for r in state if r["event"] == "collected"}
    for r in state:
        if r["event"] == "submitted" and r["batch_id"] not in collected:
            log(f"collecting earlier batch {r['batch_id']}")
            await collect(r["batch_id"], r["custom_ids"])

    for _ in range(max_rounds):
        todo = [j for j in jobs if j.custom_id not in finished]
        if not todo:
            break
        requests = [{"custom_id": j.custom_id, "params": params(j)} for j in todo]
        batch = await client.messages.batches.create(requests=requests)
        store.append("judge_batches", [{"batch_id": batch.id, "event": "submitted",
                                        "custom_ids": [j.custom_id for j in todo], "ts": now_iso()}])
        log(f"submitted batch {batch.id} with {len(todo)} requests")
        await collect(batch.id, [j.custom_id for j in todo])

    for j in jobs:
        if j.custom_id not in finished:
            p = params(j)
            ih = params_digest({"system": p.get("system"), "messages": p["messages"]})
            _record(store, order_row(j, jv, ih, status="infra", verdicts=None, raw=None,
                                     error=f"no successful batch result after {max_rounds} rounds"),
                    refusals.pop(j.custom_id, []))
