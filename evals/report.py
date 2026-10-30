"""Run report (EVALS_PROPOSAL §6.5): runs/<id>/report.md plus scoreboard/<id>.summary.json and .md."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from evals.core.storage import SCOREBOARD_DIR, RunStore, write_atomic
from evals.core.types import METRICS, MetricDef
from evals.judge import pairwise

FAKE_BANNER = (
    "> **FAKE MODE — not a real result.** Documents and judge verdicts come from the deterministic "
    "`FakeLLM`, not a model. Numbers below only show that the pipeline works."
)


# ---------------------------------------------------------------- aggregation


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Pool num/den metrics as sum(num)/sum(den); average ``value`` otherwise. ``n`` = documents."""
    ratio = [r for r in rows if r.get("den") is not None and r.get("num") is not None]
    if ratio:
        den = sum(r["den"] for r in ratio)
        return {"value": sum(r["num"] for r in ratio) / den if den else None, "n": len(ratio),
                "num": sum(r["num"] for r in ratio), "den": den}
    vals = [r["value"] for r in rows if r.get("value") is not None]
    return {"value": sum(vals) / len(vals) if vals else None, "n": len(vals)}


def metric_table(scores: list[dict[str, Any]], generators: list[str]) -> dict[str, dict[str, dict[str, Any]]]:
    """{metric: {generator: aggregate}} for every metric present."""
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in scores:
        grouped[(r["metric"], r["generator"])].append(r)
    metrics = sorted({m for m, _ in grouped})
    return {m: {g: aggregate(grouped.get((m, g), [])) for g in generators} for m in metrics}


def fmt(value: float | None, kind: str) -> str:
    if value is None:
        return "—"
    if kind == "pct":
        return f"{value * 100:.1f}%"
    if kind == "usd":
        return f"${value:.4f}"
    if kind == "ms":
        return f"{value / 1000:.1f} s"
    return f"{value:.3f}"


def cell(agg: dict[str, Any], kind: str) -> str:
    return "—" if not agg or agg.get("n", 0) == 0 else f"{fmt(agg['value'], kind)} (n={agg['n']})"


def refusal_stats(store: RunStore, generators: list[str], limit: float) -> dict[str, dict[str, Any]]:
    results = [r for r in store.iter_results() if r.generator in generators]
    errors = [e for e in store.read("errors") if e.get("phase") == "generate"]
    calls = [c for c in store.read("calls") if c.get("phase") == "generate"]
    out = {}
    for g in generators:
        mine = [r for r in results if r.generator == g]
        slots = {(r.case_id, r.rep) for r in mine}
        refused_slots = {(e["case_id"], e["rep"]) for e in errors if e["generator"] == g and e["kind"] == "refusal"}
        rate = len(refused_slots) / len(slots) if slots else 0.0
        out[g] = {
            "slots": len(slots),
            "ok": sum(1 for r in mine if not r.error),
            "errored": sum(1 for r in mine if r.error),
            "refusal_calls": sum(1 for e in errors if e["generator"] == g and e["kind"] == "refusal"),
            "slots_with_refusal": len(refused_slots),
            "refusal_rate": rate,
            "model_mismatches": sum(1 for e in errors if e["generator"] == g and e["kind"] == "model_mismatch"),
            "max_tokens_stops": sum(1 for c in calls if c["generator"] == g and c.get("stop_reason") == "max_tokens"),
            "comparable": rate <= limit,
        }
    return out


def cost_split(store: RunStore, generators: list[str]) -> dict[str, Any]:
    calls = store.read("calls")
    gen: dict[str, dict[str, Any]] = {}
    for g in generators:
        mine = [c for c in calls if c.get("phase") == "generate" and c.get("generator") == g]
        gen[g] = {
            "usd": sum(c["cost_usd"] or 0.0 for c in mine),
            "calls": len(mine),
            "unpriced_calls": sum(1 for c in mine if c["cost_usd"] is None),
            "input_tokens": sum(c["input_tokens"] for c in mine),
            "output_tokens": sum(c["output_tokens"] for c in mine),
            "cache_read_tokens": sum(c["cache_read_tokens"] for c in mine),
            "cache_write_tokens": sum(c["cache_write_tokens"] for c in mine),
        }
    judge = [c for c in calls if c.get("phase") == "judge"]
    j = {"usd": sum(c["cost_usd"] or 0.0 for c in judge), "calls": len(judge),
         "unpriced_calls": sum(1 for c in judge if c["cost_usd"] is None)}
    total_gen = sum(v["usd"] for v in gen.values())
    return {"generation": gen, "generation_usd": total_gen, "judge": j, "total_usd": total_gen + j["usd"]}


# ---------------------------------------------------------------- building


def build_summary(store: RunStore, config: dict[str, Any]) -> dict[str, Any]:
    m = store.read_manifest()
    generators = [g["name"] for g in m.get("generators", [])]
    scores = [s for s in store.read("scores") if s["generator"] in generators]
    table = metric_table(scores, generators)
    jv = m.get("judge_version")
    judge_rows = [r for r in store.read("judge") if r.get("judge_version") == jv]
    names = set(generators)
    pairs = [p for p in config["judge"]["pairs"] if p[0] in names and p[1] in names]
    wins = pairwise.summarise(judge_rows, pairs) if judge_rows else []
    refusals = refusal_stats(store, generators, float(config["run"]["refusal_rate_limit"]))
    return {
        "run_id": store.run_id,
        "mode": m.get("mode"),
        "manifest": {k: m.get(k) for k in (
            "created_at", "updated_at", "git_sha", "models", "effort", "max_tokens", "case_set",
            "case_set_hash", "case_ids", "reps", "generators", "graders", "judge_version", "judge_mode",
            "judge_batches")},
        "metrics": {k: v for k, v in table.items() if not k.startswith("trap.")},
        "traps": {k: v for k, v in table.items() if k.startswith("trap.")},
        "win_rates": wins,
        "judge_rows": len(judge_rows),
        "judge_errors": sum(1 for r in judge_rows if r.get("status") != "ok"),
        "refusals": refusals,
        "cost": cost_split(store, generators),
    }


def _metric_def(name: str) -> MetricDef:
    return METRICS.get(name, MetricDef(name, "lower" if name.startswith("trap.") else "higher", "pct" if name.startswith("trap.") else "num"))


def render_report(summary: dict[str, Any]) -> str:
    m = summary["manifest"]
    gens = [g["name"] for g in m.get("generators") or []]
    refusals = summary["refusals"]
    out = [f"# Eval run `{summary['run_id']}`", ""]
    if summary["mode"] != "live":
        out += [FAKE_BANNER, ""]
    bad = [g for g in gens if not refusals.get(g, {}).get("comparable", True)]
    if bad:
        out += [f"> **Not comparable:** {', '.join(bad)} refused above the run.refusal_rate_limit; review the cases before using these results.", ""]

    out += ["## Run", ""]
    out += [
        f"- Mode: **{summary['mode']}**; created {m.get('created_at')}; git SHA `{m.get('git_sha') or 'n/a'}`",
        f"- Models: generator `{(m.get('models') or {}).get('generator')}` (effort {(m.get('effort') or {}).get('generator')}), "
        f"judge `{(m.get('models') or {}).get('judge')}` (effort {(m.get('effort') or {}).get('judge')})",
        f"- Cases: {len(m.get('case_ids') or [])} from `{m.get('case_set')}` (hash `{(m.get('case_set_hash') or '')[:12]}`), {m.get('reps')} rep(s)",
        "- Generators: " + ", ".join(f"`{g['name']}` {g['version']}" for g in m.get("generators") or []),
        f"- Judge version: `{m.get('judge_version')}` ({m.get('judge_mode') or 'not run'}"
        + (", Message Batches" if m.get("judge_batches") else "") + ")",
        "- Graders: " + (", ".join(f"{g['name']} {g['version']}" for g in m.get("graders") or []) or "not run"),
        "",
    ]

    def gen_header(g: str) -> str:
        return g if refusals.get(g, {}).get("comparable", True) else f"{g} (not comparable)"

    out += ["## Results", "", "Ratio metrics are pooled (sum of numerators / sum of denominators); others are means. n = documents.", ""]
    out.append("| Metric | Better | " + " | ".join(gen_header(g) for g in gens) + " |")
    out.append("|---|---|" + "---|" * len(gens))
    metrics = summary["metrics"]
    for name in list(METRICS) + sorted(k for k in metrics if k not in METRICS):
        d = _metric_def(name)
        row = metrics.get(name, {})
        out.append(f"| {d.label} | {d.better} | " + " | ".join(cell(row.get(g, {}), d.fmt) for g in gens) + " |")
    for w in summary["win_rates"]:
        o = w["questions"]["overall"]
        cells = []
        for g in gens:
            if g == w["x"] and o["win_rate"] is not None:
                ci = f" ({o['ci'][0]:.2f}–{o['ci'][1]:.2f})" if o["ci"] else ""
                cells.append(f"{o['win_rate']:.2f}{ci}")
            else:
                cells.append("")
        out.append(f"| Win vs {w['y']} (overall) | higher | " + " | ".join(cells) + " |")
    out.append("")

    out += ["## Pairwise judge", ""]
    if not summary["win_rates"]:
        out += ["Not judged yet (`python -m evals judge`).", ""]
    for w in summary["win_rates"]:
        out += [f"### {w['x']} vs {w['y']}", "",
                "Both orders must agree for a win; otherwise a tie. Win rate = (wins + 0.5 × ties) / pairs; 95% cluster-bootstrap CI over cases.", "",
                f"| Question | Win rate of {w['x']} | 95% CI | W / T / L | Pairs (cases) |", "|---|---|---|---|---|"]
        for q in pairwise.QUESTIONS:
            r = w["questions"][q]
            wr = "—" if r["win_rate"] is None else f"{r['win_rate']:.2f}"
            ci = "—" if not r["ci"] else f"{r['ci'][0]:.2f}–{r['ci'][1]:.2f}"
            out.append(f"| {pairwise.QUESTION_LABELS[q]} | {wr} | {ci} | {r['wins']} / {r['ties']} / {r['losses']} | {r['pairs']} ({r['cases']}) |")
        lb = w["length_bias"]
        r_txt = "n/a" if lb["r"] is None else f"{lb['r']:+.2f}"
        flag = " — **flagged: |r| > 0.3, the judge may be rewarding length**" if lb["flag"] else ""
        out += ["", f"Length bias (overall): r = {r_txt} between {w['x']}'s score and its word-count advantage, n = {lb['n']}{flag}.", ""]
    if summary["judge_errors"]:
        out += [f"Judge errors (refusals, infra, unparseable replies; excluded): {summary['judge_errors']}", ""]

    out += ["## Traps", ""]
    traps = summary["traps"]
    if not traps:
        out += ["No trap metrics were emitted.", ""]
    else:
        out.append("| Trap | " + " | ".join(gens) + " |")
        out.append("|---|" + "---|" * len(gens))
        for name in sorted(traps):
            out.append(f"| {name[5:]} | " + " | ".join(cell(traps[name].get(g, {}), "pct") for g in gens) + " |")
        out.append("")

    out += ["## Refusals and errors", "",
            "Errors and refusals are never scored. Refusal rate = slots with at least one refused call / slots.", "",
            "| Generator | Slots | Docs OK | Errored | Refusal calls | Refusal rate | Model mismatches | max_tokens stops | Comparable |",
            "|---|---|---|---|---|---|---|---|---|"]
    for g in gens:
        r = refusals.get(g, {})
        out.append(
            f"| {g} | {r.get('slots', 0)} | {r.get('ok', 0)} | {r.get('errored', 0)} | {r.get('refusal_calls', 0)} | "
            f"{r.get('refusal_rate', 0) * 100:.1f}% | {r.get('model_mismatches', 0)} | {r.get('max_tokens_stops', 0)} | "
            f"{'yes' if r.get('comparable', True) else '**no**'} |"
        )
    out.append("")

    cost = summary["cost"]
    out += ["## Cost", "", "From the CallRecords (API usage × prices in config.toml).", "",
            "| Phase | Calls | Input tok | Output tok | Cache read | Cache write | USD |", "|---|---|---|---|---|---|---|"]
    for g in gens:
        c = cost["generation"][g]
        out.append(f"| generate: {g} | {c['calls']} | {c['input_tokens']:,} | {c['output_tokens']:,} | "
                   f"{c['cache_read_tokens']:,} | {c['cache_write_tokens']:,} | ${c['usd']:.4f} |")
    j = cost["judge"]
    note = " (Batches: 50% applied)" if m.get("judge_batches") else ""
    out.append(f"| judge{note} | {j['calls']} | | | | | ${j['usd']:.4f} |")
    out.append(f"| **total** | | | | | | **${cost['total_usd']:.4f}** |")
    out.append(f"\nGeneration ${cost['generation_usd']:.4f} · judging ${j['usd']:.4f}.")
    unpriced = sum(c["unpriced_calls"] for c in cost["generation"].values()) + j["unpriced_calls"]
    if unpriced:
        out.append(f"\n{unpriced} call(s) used an unpriced model and are not in these totals.")
    out.append("")
    return "\n".join(out)


def write_report(store: RunStore, config: dict[str, Any], *, scoreboard_dir: Path = SCOREBOARD_DIR) -> tuple[Path, Path]:
    summary = build_summary(store, config)
    text = render_report(summary)
    report = store.write_text("report.md", text)
    scoreboard_dir = Path(scoreboard_dir)
    summary_path = scoreboard_dir / f"{store.run_id}.summary.json"
    write_atomic(summary_path, json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    write_atomic(scoreboard_dir / f"{store.run_id}.md", text)
    return report, summary_path
