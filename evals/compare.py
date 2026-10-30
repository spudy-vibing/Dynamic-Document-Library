"""``python -m evals compare <run_a> <run_b>``: paired per-metric deltas with cluster-bootstrap CIs (EVAL-16).

Deltas are run_b minus run_a, per generator present in both runs, over the cases both
runs scored. The CI resamples cases (clusters) with replacement. A regression is a
metric that got worse with a CI excluding 0.
"""

from __future__ import annotations

import random
from collections import defaultdict
from typing import Any

from evals.core.storage import RunStore
from evals.core.types import METRICS
from evals.judge import pairwise
from evals.report import fmt

Clusters = dict[str, tuple[float, float]]  # case_id -> (num, den)


def per_case(rows: list[dict[str, Any]]) -> Clusters:
    """Per case: (sum num, sum den) for ratio metrics, else (sum value, count)."""
    out: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
    ratio = any(r.get("den") is not None and r.get("num") is not None for r in rows)
    for r in rows:
        if ratio:
            if r.get("den") is None or r.get("num") is None:
                continue
            out[r["case_id"]][0] += r["num"]
            out[r["case_id"]][1] += r["den"]
        elif r.get("value") is not None:
            out[r["case_id"]][0] += r["value"]
            out[r["case_id"]][1] += 1
    return {k: (v[0], v[1]) for k, v in out.items() if v[1]}


def _pool(clusters: list[tuple[float, float]]) -> float | None:
    den = sum(d for _, d in clusters)
    return sum(n for n, _ in clusters) / den if den else None


def paired_bootstrap_delta(
    a: Clusters, b: Clusters, *, resamples: int = pairwise.BOOTSTRAP_RESAMPLES, seed: int = pairwise.BOOTSTRAP_SEED
) -> dict[str, Any] | None:
    cases = sorted(set(a) & set(b))
    if not cases:
        return None
    pa, pb = _pool([a[c] for c in cases]), _pool([b[c] for c in cases])
    if pa is None or pb is None:
        return None
    rng = random.Random(seed)
    stats = []
    for _ in range(resamples):
        pick = [cases[rng.randrange(len(cases))] for _ in cases]
        va, vb = _pool([a[c] for c in pick]), _pool([b[c] for c in pick])
        if va is not None and vb is not None:
            stats.append(vb - va)
    stats.sort()
    lo = stats[int(0.025 * (len(stats) - 1))]
    hi = stats[int(round(0.975 * (len(stats) - 1)))]
    return {"a": pa, "b": pb, "delta": pb - pa, "ci": [lo, hi], "cases": len(cases)}


def direction(metric: str) -> str | None:
    if metric in METRICS:
        return METRICS[metric].better
    if metric.startswith("trap."):
        return "lower"
    return None


#: Reported in the comparison but never flagged: wall time depends on machine load and
#: network, so two identical runs can differ by chance.
NOT_GATED = frozenset({"wall_ms"})


def is_regression(metric: str, d: dict[str, Any]) -> bool:
    if metric in NOT_GATED:
        return False
    better = direction(metric)
    lo, hi = d["ci"]
    if better == "lower":
        return d["delta"] > 0 and lo > 0
    if better == "higher":
        return d["delta"] < 0 and hi < 0
    return False


def compare_runs(a: RunStore, b: RunStore, config: dict[str, Any], **bootstrap: Any) -> dict[str, Any]:
    ma, mb = a.read_manifest(), b.read_manifest()
    warnings = []
    for key in ("mode", "models", "effort", "case_set_hash"):
        if ma.get(key) != mb.get(key):
            warnings.append(f"{key} differs: {ma.get(key)!r} vs {mb.get(key)!r}")
    va = {g["name"]: g["version"] for g in ma.get("generators", [])}
    vb = {g["name"]: g["version"] for g in mb.get("generators", [])}
    gens = [g for g in va if g in vb]
    for g in gens:
        if va[g] != vb[g]:
            warnings.append(f"{g} version differs: {va[g]} vs {vb[g]}")

    sa, sb = a.read("scores"), b.read("scores")
    rows = []
    for g in gens:
        metrics = sorted({r["metric"] for r in sa if r["generator"] == g} & {r["metric"] for r in sb if r["generator"] == g})
        for metric in metrics:
            d = paired_bootstrap_delta(
                per_case([r for r in sa if r["generator"] == g and r["metric"] == metric]),
                per_case([r for r in sb if r["generator"] == g and r["metric"] == metric]),
                **bootstrap,
            )
            if d:
                rows.append({"generator": g, "metric": metric, **d, "regression": is_regression(metric, d)})

    wins = []
    jva, jvb = ma.get("judge_version"), mb.get("judge_version")
    if jva and jva != jvb:
        warnings.append(f"judge_version differs ({jva} vs {jvb}): win rates are not compared (EVAL-12)")
    elif jva:
        oa = pairwise.combine(r for r in a.read("judge") if r.get("judge_version") == jva)
        ob = pairwise.combine(r for r in b.read("judge") if r.get("judge_version") == jvb)
        for x, y in config["judge"]["pairs"]:
            for q in pairwise.QUESTIONS:
                ca = _win_clusters([o for o in oa if (o.x, o.y) == (x, y)], q)
                cb = _win_clusters([o for o in ob if (o.x, o.y) == (x, y)], q)
                d = paired_bootstrap_delta(ca, cb, **bootstrap)
                if d:
                    reg = d["delta"] < 0 and d["ci"][1] < 0
                    wins.append({"x": x, "y": y, "question": q, **d, "regression": reg})

    regressions = [r for r in rows + wins if r["regression"]]
    return {"run_a": a.run_id, "run_b": b.run_id, "warnings": warnings, "metrics": rows, "wins": wins,
            "regressions": len(regressions)}


def _win_clusters(outcomes: list[pairwise.PairOutcome], question: str) -> Clusters:
    out: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
    for o in outcomes:
        out[o.case_id][0] += o.score_x(question)
        out[o.case_id][1] += 1
    return {k: (v[0], v[1]) for k, v in out.items()}


def render_compare(c: dict[str, Any]) -> str:
    out = [f"# Compare `{c['run_a']}` → `{c['run_b']}`", "",
           "Delta = B − A over cases scored in both runs; 95% cluster-bootstrap CI over cases. "
           "**REGRESSION** = worse, with the CI excluding 0.", ""]
    for w in c["warnings"]:
        out.append(f"> Warning: {w}")
    if c["warnings"]:
        out.append("")
    out += ["| Generator | Metric | A | B | Δ | 95% CI | Cases | |", "|---|---|---|---|---|---|---|---|"]
    for r in c["metrics"]:
        kind = METRICS[r["metric"]].fmt if r["metric"] in METRICS else ("pct" if r["metric"].startswith("trap.") else "num")
        dkind = "num" if kind == "usd" else kind
        out.append(
            f"| {r['generator']} | {r['metric']} | {fmt(r['a'], kind)} | {fmt(r['b'], kind)} | {fmt(r['delta'], dkind)} | "
            f"{fmt(r['ci'][0], dkind)} to {fmt(r['ci'][1], dkind)} | {r['cases']} | {'**REGRESSION**' if r['regression'] else ''} |"
        )
    if c["wins"]:
        out += ["", "| Pair | Question | A | B | Δ | 95% CI | Cases | |", "|---|---|---|---|---|---|---|---|"]
        for r in c["wins"]:
            out.append(
                f"| {r['x']} vs {r['y']} | {pairwise.QUESTION_LABELS[r['question']]} | {r['a']:.2f} | {r['b']:.2f} | "
                f"{r['delta']:+.2f} | {r['ci'][0]:+.2f} to {r['ci'][1]:+.2f} | {r['cases']} | {'**REGRESSION**' if r['regression'] else ''} |"
            )
    out += ["", f"Regressions: **{c['regressions']}**", ""]
    return "\n".join(out)
