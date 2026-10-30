"""Run orchestration: projection, generation, grading and judging (EVAL-15, EVAL-18, EVAL-20).

The CLI (``evals/__main__.py``) is a thin layer over these functions; tests call them
directly with in-memory cases and temporary run directories.
"""

from __future__ import annotations

import asyncio
import os
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Iterable

from evals.core.cases import load_case, load_case_set
from evals.core.llm import AnthropicLLM, ClaudeCodeLLM, FakeLLM, ServedModelMismatch, make_client
from evals.core.storage import (
    RunStore,
    case_path_ref,
    case_set_hash,
    git_sha,
    now_iso,
    resolve_case_path,
)
from evals.core.types import (
    LLM,
    EvalCase,
    GenerationResult,
    Generator,
    Grader,
    InfraError,
    RefusalError,
)
from evals.generators import GENERATORS, NO_LLM, PROTOTYPES
from evals.judge import pairwise

Log = Callable[[str], None]


class BudgetExceeded(RuntimeError):
    """The expected cost of a live run is above run.max_usd (EVAL-20)."""


class MissingKey(RuntimeError):
    """A live run without ANTHROPIC_API_KEY in the environment."""


# ---------------------------------------------------------------- selection


def select_generators(names: Iterable[str] | None, default: Iterable[str]) -> list[Generator]:
    names = list(names or default)
    unknown = [n for n in names if n not in GENERATORS]
    if unknown:
        raise SystemExit(f"unknown generator(s): {', '.join(unknown)}; known: {', '.join(GENERATORS)}")
    return [GENERATORS[n] for n in names]


def select_cases(case_set: str, case_ids: Iterable[str] | None = None) -> list[EvalCase]:
    cases = load_case_set(case_set)
    if not cases:
        raise SystemExit(f"no cases found in evals/cases/{case_set}/")
    if case_ids:
        wanted = list(case_ids)
        by_id = {c.id: c for c in cases}
        missing = [i for i in wanted if i not in by_id]
        if missing:
            raise SystemExit(f"unknown case(s): {', '.join(missing)}")
        cases = [by_id[i] for i in wanted]
    return cases


def cases_from_manifest(manifest: dict[str, Any]) -> list[EvalCase]:
    return [load_case(resolve_case_path(p)) for p in manifest.get("case_paths", [])]


def new_run_id(mode: str) -> str:
    return f"{mode}-{datetime.now():%Y%m%d-%H%M%S}"


class MissingCli(RuntimeError):
    """A --cli run without the ``claude`` executable on PATH."""


def require_cli() -> None:
    import shutil

    if shutil.which("claude") is None:
        raise MissingCli("--cli needs the Claude Code CLI (`claude`) on PATH, logged in to a subscription")


def require_key() -> None:
    """Live runs use the owner's key from the environment; the harness never reads or stores it."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise MissingKey("--live needs ANTHROPIC_API_KEY in the environment (the owner's key; agents never hold it)")


# ---------------------------------------------------------------- cost projection (EVAL-20)


@dataclass
class ProjectionLine:
    label: str
    docs: int
    calls: int
    input_tokens: int
    output_expected: int
    output_worst: int
    usd_expected: float
    usd_worst: float
    note: str = ""


@dataclass
class Projection:
    lines: list[ProjectionLine] = field(default_factory=list)
    max_usd: float = 0.0
    assumptions: list[str] = field(default_factory=list)

    @property
    def expected(self) -> float:
        return sum(line.usd_expected for line in self.lines)

    @property
    def worst(self) -> float:
        return sum(line.usd_worst for line in self.lines)

    @property
    def generation_expected(self) -> float:
        return sum(line.usd_expected for line in self.lines if not line.label.startswith("judge"))

    def format(self) -> str:
        head = f"{'':34} {'docs':>5} {'calls':>6} {'input tok':>11} {'output tok (exp)':>17} {'$ expected':>11} {'$ worst':>9}"
        rows = [head, "-" * len(head)]
        for ln in self.lines:
            rows.append(
                f"{ln.label:34} {ln.docs or '':>5} {ln.calls:>6} {ln.input_tokens:>11,} {ln.output_expected:>17,} "
                f"{ln.usd_expected:>11.2f} {ln.usd_worst:>9.2f}" + (f"  {ln.note}" if ln.note else "")
            )
        rows.append("-" * len(head))
        rows.append(f"{'TOTAL':34} {'':>5} {'':>6} {'':>11} {'':>17} {self.expected:>11.2f} {self.worst:>9.2f}")
        status = "OK" if self.expected <= self.max_usd else "EXCEEDS CAP"
        rows.append(f"\nCap run.max_usd = ${self.max_usd:.2f}: expected ${self.expected:.2f} -> {status}")
        rows += ["", "Assumptions:"] + [f"- {a}" for a in self.assumptions]
        return "\n".join(rows)


def _price(config: dict[str, Any], model: str) -> dict[str, float]:
    p = config.get("prices", {}).get(model)
    if p is None:
        raise SystemExit(f"model {model} has no price in config.toml; cannot project cost")
    return p


def visible_chars(section: Any) -> int:
    words = section.constraints.max_words or 200
    return words * 6


def project(
    cases: list[EvalCase], generators: list[Generator], reps: int, config: dict[str, Any], *,
    judge: bool = True, batches: bool | None = None,
) -> Projection:
    """Expected cost uses [projection] (no prompt-cache discount); worst case uses max_tokens on every call."""
    proj_cfg = config["projection"]
    cpt = float(proj_cfg["chars_per_token"])
    out_per_call = int(proj_cfg["output_tokens_per_call"])
    gen_model = config["models"]["generator"]
    gp = _price(config, gen_model)
    gen_max = int(config["max_tokens"]["generator"])
    proj = Projection(max_usd=float(config["run"]["max_usd"]))

    for g in generators:
        calls = in_tok = out_exp = out_worst = 0
        if g.name not in NO_LLM:
            for case in cases:
                per_sec = int(sum(visible_chars(s) for s in case.sections) / max(1, len(case.sections)))
                plan = g.planned_calls(case, per_sec) if hasattr(g, "planned_calls") else [per_sec * 4] * len(case.sections)
                extra_sections = max(0, len(case.sections) - len(plan))  # a one-shot call writes them all
                calls += len(plan) * reps
                in_tok += int(sum(plan) / cpt) * reps
                out_exp += (len(plan) * out_per_call + int(extra_sections * per_sec / cpt)) * reps
                out_worst += len(plan) * gen_max * reps
        usd = lambda i, o: (i * gp["input"] + o * gp["output"]) / 1e6  # noqa: E731
        proj.lines.append(
            ProjectionLine(g.name, len(cases) * reps, calls, in_tok, out_exp, out_worst, usd(in_tok, out_exp), usd(in_tok, out_worst))
        )

    if judge:
        names = {g.name for g in generators}
        jm = config["models"]["judge"]
        jp = _price(config, jm)
        use_batches = config["judge"].get("use_batches") if batches is None else batches
        factor = pairwise.BATCH_COST_FACTOR if use_batches else 1.0
        j_out = int(proj_cfg["judge_output_tokens"])
        j_max = int(config["max_tokens"]["judge"])
        for x, y in config["judge"]["pairs"]:
            if x not in names or y not in names:
                continue
            calls = in_tok = 0
            for case in cases:
                doc = "word " * int(sum(visible_chars(s) for s in case.sections) / 5)
                system, messages = pairwise.build_request(case, doc, doc)
                chars = len(system) + sum(len(b["text"]) for b in messages[0]["content"])
                calls += 2 * reps
                in_tok += int(chars / cpt) * 2 * reps
            usd = lambda i, o: factor * (i * jp["input"] + o * jp["output"]) / 1e6  # noqa: E731
            proj.lines.append(
                ProjectionLine(f"judge {x} vs {y}", 0, calls, in_tok, calls * j_out, calls * j_max,
                               usd(in_tok, calls * j_out), usd(in_tok, calls * j_max),
                               note="(batches, 50%)" if factor < 1 else "")
            )

    proj.assumptions = [
        f"generator {gen_model}, effort {config['effort']['generator']}; judge {config['models']['judge']}, effort {config['effort']['judge']}",
        f"expected: {out_per_call} output tokens per generator call (thinking included), "
        f"{proj_cfg['judge_output_tokens']} per judge call; a one-shot call also writes the other sections' text",
        f"worst case: max_tokens on every call ({gen_max} generator, {config['max_tokens']['judge']} judge)",
        f"input tokens = prompt characters / {cpt:g}; no prompt-cache discount assumed (conservative)",
        "refusal retries and transient-error retries are not included",
    ]
    if any(g.name in PROTOTYPES for g in generators):
        proj.assumptions.append(
            "prototypes: the _brief and _editor calls are planned like section calls (same expected output); "
            "P3's prefix grows by each earlier section; the editor's one JSON retry is not included"
        )
    return proj


def judge_only(proj: Projection) -> Projection:
    return Projection([ln for ln in proj.lines if ln.label.startswith("judge")], proj.max_usd, proj.assumptions)


def check_budget(proj: Projection) -> None:
    if proj.expected > proj.max_usd:
        raise BudgetExceeded(
            f"expected cost ${proj.expected:.2f} exceeds run.max_usd ${proj.max_usd:.2f}; not starting. "
            "Reduce cases/reps/generators or have the owner raise max_usd in evals/config.toml."
        )


# ---------------------------------------------------------------- manifest


def build_manifest(
    cases: list[EvalCase], generators: list[Generator], reps: int, config: dict[str, Any], *, mode: str, case_set: str
) -> dict[str, Any]:
    return {
        "mode": mode,
        "git_sha": git_sha(),
        "generators": [{"name": g.name, "version": g.version} for g in generators],
        "judge_version": pairwise.judge_version(config),
        "models": dict(config["models"]),
        "effort": dict(config["effort"]),
        "max_tokens": dict(config["max_tokens"]),
        "case_set": case_set,
        "case_ids": [c.id for c in cases],
        "case_paths": [case_path_ref(c) for c in cases],
        "case_set_hash": case_set_hash(cases),
        "reps": reps,
    }


# ---------------------------------------------------------------- generation


def error_result(g: Generator, case: EvalCase, rep: int, llm: LLM, wall_ms: int, error: str) -> GenerationResult:
    return GenerationResult(
        generator=g.name, generator_version=g.version, case_id=case.id, rep=rep, title=case.title,
        sections=[], calls=list(llm.calls), wall_ms=wall_ms, error=error,
    )


def make_llm_factory(
    config: dict[str, Any], *, live: bool, cli: bool = False, client: Any = None, limiter: asyncio.Semaphore | None = None
) -> Callable[[], LLM]:
    model, effort, mt = config["models"]["generator"], config["effort"]["generator"], config["max_tokens"]["generator"]
    if cli:
        return lambda: ClaudeCodeLLM(model, effort, mt, config, limiter=limiter)
    if not live:
        return lambda: FakeLLM(model, effort, mt, config)
    return lambda: AnthropicLLM(model, effort, mt, config, client=client, limiter=limiter)


async def run_generation(
    store: RunStore,
    cases: list[EvalCase],
    generators: list[Generator],
    reps: int,
    config: dict[str, Any],
    *,
    live: bool = False,
    cli: bool = False,
    client: Any = None,
    make_llm: Callable[[], LLM] | None = None,
    log: Log = print,
) -> dict[str, int]:
    """Generate every (generator, case, rep) slot not already completed. Returns counts."""
    conc = int(config["run"]["max_concurrency"])
    owned = live and client is None and make_llm is None
    if owned:
        client = make_client()
    limiter = asyncio.Semaphore(conc)  # bounds in-flight API calls across all slots
    make_llm = make_llm or make_llm_factory(config, live=live, cli=cli, client=client, limiter=limiter)
    slot_sem = asyncio.Semaphore(conc)
    slots = [
        (g, case, rep)
        for g in generators
        for case in cases
        for rep in range(1, reps + 1)
        if not store.has_result(g.name, case.id, rep)
    ]
    counts = {"slots": len(slots), "ok": 0, "errors": 0, "skipped_done": len(generators) * len(cases) * reps - len(slots)}
    if counts["skipped_done"]:
        log(f"resuming: {counts['skipped_done']} slot(s) already complete")

    async def one(g: Generator, case: EvalCase, rep: int) -> None:
        async with slot_sem:
            llm = make_llm()
            t0 = time.perf_counter()
            kind = None
            try:
                result = await g.generate(case, llm, rep)
            except RefusalError as e:
                kind, msg = "slot_refused", f"refusal: {e}"
            except ServedModelMismatch as e:
                kind, msg = "model_mismatch", f"infra: {e}"
            except InfraError as e:
                kind, msg = "infra", f"infra: {e}"
            except Exception as e:  # noqa: BLE001 - a generator bug must not stop the run
                kind, msg = "exception", f"exception: {type(e).__name__}: {e}"
                log(traceback.format_exc())
            if kind is not None:
                result = error_result(g, case, rep, llm, int((time.perf_counter() - t0) * 1000), msg)
            store.save_result(result)
            slot = {"generator": g.name, "case_id": case.id, "rep": rep}
            store.append("calls", [{"phase": "generate", **slot, **c.to_dict()} for c in result.calls])
            errors = [
                {"phase": "generate", "kind": "refusal", **slot, "category": c.refusal_category, "node_id": c.node_id, "ts": now_iso()}
                for c in result.calls
                if c.status == "refusal"
            ]
            if kind is not None:
                errors.append({"phase": "generate", "kind": kind, **slot, "message": result.error, "ts": now_iso()})
                counts["errors"] += 1
                log(f"  {g.name} {case.id} rep {rep}: {result.error}")
            else:
                counts["ok"] += 1
            store.append("errors", errors)

    try:
        await asyncio.gather(*(one(*s) for s in slots))
    finally:
        if owned:
            await client.close()
    store.touch_manifest(generation_finished_at=now_iso())
    return counts


# ---------------------------------------------------------------- grading


HARNESS_METRICS = ("cost_usd", "cache_hit", "wall_ms")


def harness_scores(result: GenerationResult) -> dict[str, dict[str, Any]]:
    """Cost, cache hit and wall time from the CallRecords, used only when no grader emits them."""
    costs = [c.cost_usd for c in result.calls]
    read = sum(c.cache_read_tokens for c in result.calls)
    den = read + sum(c.cache_write_tokens + c.input_tokens for c in result.calls)
    return {
        "cost_usd": {"value": None if any(x is None for x in costs) else sum(costs)},  # type: ignore[arg-type]
        "cache_hit": {"value": read / den if den else None, "num": read, "den": den},
        "wall_ms": {"value": float(result.wall_ms)},
    }


def grade_run(store: RunStore, cases: list[EvalCase], graders: list[Grader], *, log: Log = print) -> int:
    """Apply every grader to every stored, error-free result; rewrite scores.jsonl."""
    by_id = {c.id: c for c in cases}
    rows: list[dict[str, Any]] = []
    errors = []
    for result in store.iter_results():
        if result.error or result.case_id not in by_id:
            continue
        case = by_id[result.case_id]
        base = {"generator": result.generator, "generator_version": result.generator_version,
                "case_id": result.case_id, "rep": result.rep}
        seen = set()
        for grader in graders:
            try:
                scores = grader.grade(case, result)
            except Exception as e:  # noqa: BLE001
                errors.append({"phase": "grade", "kind": "grader", **base, "grader": grader.name,
                               "message": f"{type(e).__name__}: {e}", "ts": now_iso()})
                continue
            for s in scores:
                seen.add(s.metric)
                rows.append({**base, "metric": s.metric, "value": s.value, "num": s.num, "den": s.den,
                             "detail": s.detail, "grader": grader.name, "grader_version": grader.version})
        for metric, vals in harness_scores(result).items():
            if metric not in seen:
                rows.append({**base, "metric": metric, "value": vals["value"], "num": vals.get("num"),
                             "den": vals.get("den"), "detail": {}, "grader": "harness", "grader_version": "1"})
    store.rewrite("scores", rows)
    store.append("errors", errors)
    for e in errors:
        log(f"  grader {e['grader']} failed on {e['generator']} {e['case_id']} rep {e['rep']}: {e['message']}")
    store.touch_manifest(graded_at=now_iso(), graders=[{"name": g.name, "version": g.version} for g in graders])
    return len(rows)


# ---------------------------------------------------------------- judging


async def judge_run(
    store: RunStore,
    cases: list[EvalCase],
    config: dict[str, Any],
    *,
    live: bool = False,
    cli: bool = False,
    client: Any = None,
    log: Log = print,
    poll_s: float = 30.0,
) -> dict[str, int]:
    """Pairwise-judge every pair in config judge.pairs; append judge.jsonl (resumable)."""
    jv = pairwise.judge_version(config)
    manifest = store.read_manifest()
    names = {g["name"] for g in manifest.get("generators", [])}
    pairs = [p for p in config["judge"]["pairs"] if p[0] in names and p[1] in names]
    tasks, skipped = pairwise.plan_pairs(store.iter_results(), pairs, seed=store.run_id)
    done = {r["custom_id"] for r in store.read("judge") if r.get("status") == "ok" and r.get("judge_version") == jv}
    jobs = pairwise.pending_jobs(tasks, {c.id: c for c in cases}, done)
    log(f"judge {jv}: {len(tasks)} pair(s), {len(jobs)} order(s) to judge, {len(skipped)} skipped")
    owned = live and jobs and client is None
    if owned:
        client = make_client()
    try:
        await _judge_jobs(jobs, store, config, jv, live=live, cli=cli, client=client, log=log, poll_s=poll_s)
    finally:
        if owned:
            await client.close()
    store.touch_manifest(judge_version=jv, judged_at=now_iso(), judge_mode="live" if live else "cli" if cli else "fake",
                         judge_batches=bool(live and config["judge"].get("use_batches")))
    return {"pairs": len(tasks), "orders": len(jobs), "skipped": len(skipped)}


async def _judge_jobs(
    jobs: list[pairwise.JudgeJob], store: RunStore, config: dict[str, Any], jv: str, *,
    live: bool, client: Any, log: Log, poll_s: float, cli: bool = False,
) -> None:
    if jobs:
        if cli:  # one headless call per order; batches do not apply
            limiter = asyncio.Semaphore(int(config["run"]["max_concurrency"]))
            jm, je, jt = config["models"]["judge"], config["effort"]["judge"], config["max_tokens"]["judge"]
            await pairwise.judge_with_llm(
                jobs, lambda: ClaudeCodeLLM(jm, je, jt, config, limiter=limiter), store, jv,
                concurrency=int(config["run"]["max_concurrency"]),
            )
        elif live and config["judge"].get("use_batches"):
            await pairwise.judge_with_batches(jobs, client, config, store, jv, poll_s=poll_s, log=log)
        elif live:
            limiter = asyncio.Semaphore(int(config["run"]["max_concurrency"]))
            jm, je, jt = config["models"]["judge"], config["effort"]["judge"], config["max_tokens"]["judge"]
            await pairwise.judge_with_llm(
                jobs, lambda: AnthropicLLM(jm, je, jt, config, client=client, limiter=limiter), store, jv,
                concurrency=int(config["run"]["max_concurrency"]),
            )
        else:
            await pairwise.judge_with_llm(jobs, pairwise.make_fake_judge(config), store, jv)
