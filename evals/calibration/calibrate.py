"""Judge calibration: agreement between the judges and the human labels (WP-018, EVAL-13).

Thresholds (EVALS_PROPOSAL §6.3, BRD EVAL-13; ``[calibration]`` in config.toml):

- **Pairwise**: agreement with the human majority on non-tie items >= 0.75 for every gated
  question (``gated_questions``, default the overall verdict, which the win-rate gates use).
  A *non-tie item* is one where the human majority chose a side; the judge's verdict is its
  combined both-orders verdict (a win only when both orders agree, else a tie), so a judge tie
  on such an item counts as a disagreement. The human majority is the verdict more than half of
  the labellers gave; without one (e.g. two labellers who disagree) the item is a human tie.
  The other questions are reported the same way and marked as not trusted when below 0.75.
- **Absolute**: quadratic-weighted kappa >= 0.6 per criterion, judge score against each
  labeller's score (pooled over labellers). A criterion below 0.6 is reported but not gated:
  it is listed as not trusted, and gates should not rely on it until recalibrated.
- **Label counts**: at least ``min_pairwise_labels`` (40) labelled pairs and
  ``min_absolute_labels`` (20) labelled documents that the judge has also scored; with
  ``recalibration`` (judge model or rubric changed) at least ``recalibration_min_labels`` (10)
  of each.

Also reported: three-way raw agreement and Cohen's kappa (A / B / tie) per question; exact and
within-1 agreement and the mean score difference (judge - human) per criterion; per-labeller
figures; and human-vs-human agreement for every pair of labellers, the ceiling the judge is
measured against. Every interval is a 95% cluster-bootstrap CI over cases.

Single-labeller mode (one labeller ID): the "human majority" is that one person, and nothing
human-vs-human can be computed, so there is no estimate of how far humans agree with each other.
The result is computed and the thresholds applied, but it is marked **provisional**: the plan
expects the owner plus one other person (BRD §1.7). Runs that are not live are also provisional.
"""

from __future__ import annotations

import json
from itertools import combinations
from typing import Any

from evals.calibration import agreement as ag
from evals.calibration.labels import read_labels
from evals.calibration.pack import settings
from evals.core.storage import RunStore, now_iso, write_atomic
from evals.judge import absolute, pairwise

PC, SC = ag.PAIR_CATEGORIES, ag.SCORE_CATEGORIES


def _latest_labels(rows: list[dict[str, Any]], labellers: list[str] | None) -> tuple[dict, dict]:
    """(pairwise, absolute): slot -> {labeller: winners | scores}, the last import winning."""
    pw: dict[tuple, dict[str, dict[str, str]]] = {}
    ab: dict[tuple, dict[str, dict[str, int]]] = {}
    for r in rows:
        if labellers and r["labeller"] not in labellers:
            continue
        if r["kind"] == "pairwise":
            pw.setdefault((r["case_id"], r["rep"], r["pair"][0], r["pair"][1]), {})[r["labeller"]] = r["winners"]
        else:
            ab.setdefault((r["generator"], r["case_id"], r["rep"]), {})[r["labeller"]] = {
                c: int(v) for c, v in r["scores"].items()}
    return pw, ab


def _judge_pairwise(store: RunStore, jv: str | None) -> dict[tuple, dict[str, str]]:
    rows = [r for r in store.read("judge") if r.get("judge_version") == jv]
    out = {}
    for o in pairwise.combine(rows):
        out[(o.case_id, o.rep, o.x, o.y)] = {
            q: "tie" if o.winners[q] is None else "x" if o.winners[q] == o.x else "y" for q in pairwise.QUESTIONS}
    return out


def majority(votes: list[str]) -> tuple[str, bool]:
    """(verdict, had_majority): the verdict more than half the labellers gave, else a tie."""
    for v in PC:
        if votes.count(v) * 2 > len(votes):
            return v, True
    return "tie", False


def _clusters(points: list[tuple[str, Any, Any]], cats: tuple) -> dict[str, ag.Matrix]:
    """case_id -> confusion matrix of (reference, other) points."""
    by_case: dict[str, list[tuple[Any, Any]]] = {}
    for case_id, a, b in points:
        by_case.setdefault(case_id, []).append((a, b))
    return {c: ag.confusion(v, cats) for c, v in by_case.items()}


def _pairwise_section(human: dict, judge: dict, cfg: dict[str, Any], boot: dict[str, Any]) -> dict[str, Any]:
    labellers = sorted({l for v in human.values() for l in v})
    usable = sorted(k for k in human if k in judge)
    questions = {}
    for q in pairwise.QUESTIONS:
        pts, no_majority = [], 0
        per_labeller: dict[str, list[tuple[str, str, str]]] = {l: [] for l in labellers}
        for slot in usable:
            votes = [human[slot][l][q] for l in sorted(human[slot])]
            ref, had = majority(votes)
            no_majority += not had
            pts.append((slot[0], ref, judge[slot][q]))
            for l, w in human[slot].items():
                per_labeller[l].append((slot[0], w[q], judge[slot][q]))
        cl = _clusters(pts, PC)
        m = ag.pooled(cl, len(PC))
        agree = ag.estimate(cl, len(PC), ag.decided_agreement, **boot)
        questions[q] = {
            "items": ag.total(m),
            "non_tie_items": ag.decided_count(m),
            "judge_ties_on_non_tie": m[0][2] + m[1][2],
            "no_human_majority": no_majority,
            "agreement": agree,
            "raw_agreement": ag.estimate(cl, len(PC), ag.raw_agreement, **boot),
            "kappa": ag.estimate(cl, len(PC), ag.cohen_kappa, **boot),
            "confusion": m,
            "gated": q in cfg["gated_questions"],
            "passes": agree["value"] is not None and agree["value"] >= float(cfg["pairwise_agreement"]),
            "per_labeller": {l: ag.decided_agreement(ag.pooled(_clusters(p, PC), len(PC))) for l, p in per_labeller.items()},
        }
    hh = []
    for a, b in combinations(labellers, 2):
        both = [s for s in human if a in human[s] and b in human[s]]
        per_q = {}
        for q in pairwise.QUESTIONS:
            cl = _clusters([(s[0], human[s][a][q], human[s][b][q]) for s in both], PC)
            per_q[q] = {"raw_agreement": ag.estimate(cl, len(PC), ag.raw_agreement, **boot),
                        "kappa": ag.estimate(cl, len(PC), ag.cohen_kappa, **boot),
                        "both_sided_agreement": ag.both_decided_agreement(ag.pooled(cl, len(PC)))}
        hh.append({"labellers": [a, b], "items": len(both), "questions": per_q})
    return {"labelled": len(human), "usable": len(usable), "unjudged": len(human) - len(usable),
            "labellers": labellers, "questions": questions, "human_vs_human": hh}


def _absolute_section(human: dict, judge: dict, cfg: dict[str, Any], boot: dict[str, Any]) -> dict[str, Any]:
    labellers = sorted({l for v in human.values() for l in v})
    usable = sorted(k for k in human if k in judge and all(c in judge[k] for c in absolute.CRITERIA))
    criteria = {}
    for c in absolute.CRITERIA:
        pts = [(slot[1], human[slot][l][c], judge[slot][c]) for slot in usable for l in sorted(human[slot])]
        cl = _clusters(pts, SC)
        m = ag.pooled(cl, len(SC))
        kappa = ag.estimate(cl, len(SC), ag.weighted_kappa, **boot)
        criteria[c] = {
            "pairs": ag.total(m),
            "kappa": kappa,
            "exact": ag.raw_agreement(m),
            "within_one": ag.within_one(m),
            "mean_diff": ag.mean_difference(m),
            "trusted": kappa["value"] is not None and kappa["value"] >= float(cfg["absolute_kappa"]),
            "per_labeller": {
                l: ag.weighted_kappa(ag.confusion([(human[s][l][c], judge[s][c]) for s in usable if l in human[s]], SC))
                for l in labellers},
        }
    hh = []
    for a, b in combinations(labellers, 2):
        both = [s for s in human if a in human[s] and b in human[s]]
        per_c = {}
        for c in absolute.CRITERIA:
            cl = _clusters([(s[1], human[s][a][c], human[s][b][c]) for s in both], SC)
            m = ag.pooled(cl, len(SC))
            per_c[c] = {"kappa": ag.estimate(cl, len(SC), ag.weighted_kappa, **boot),
                        "exact": ag.raw_agreement(m), "within_one": ag.within_one(m)}
        hh.append({"labellers": [a, b], "items": len(both), "criteria": per_c})
    return {"labelled": len(human), "usable": len(usable), "unjudged": len(human) - len(usable),
            "labellers": labellers, "criteria": criteria, "human_vs_human": hh}


def calibrate(
    store: RunStore, config: dict[str, Any], *, recalibration: bool = False, labellers: list[str] | None = None,
    resamples: int | None = None,
) -> dict[str, Any]:
    """The calibration summary for ``store``'s judge versions and imported labels."""
    cfg = settings(config)
    manifest = store.read_manifest()
    jv = manifest.get("judge_version")
    av = manifest.get("absolute_version") or absolute.absolute_version(config)
    boot = {"resamples": int(resamples if resamples is not None else cfg["bootstrap_resamples"])}
    rows = read_labels(store)
    pw_h, ab_h = _latest_labels(rows, labellers)
    pw = _pairwise_section(pw_h, _judge_pairwise(store, jv), cfg, boot)
    ab = _absolute_section(ab_h, absolute.doc_scores(store.read(absolute.STREAM), av), cfg, boot)
    who = sorted(set(pw["labellers"]) | set(ab["labellers"]))

    min_pw = int(cfg["recalibration_min_labels"] if recalibration else cfg["min_pairwise_labels"])
    min_ab = int(cfg["recalibration_min_labels"] if recalibration else cfg["min_absolute_labels"])
    counts_ok = pw["usable"] >= min_pw and ab["usable"] >= min_ab
    gated = [q for q in pairwise.QUESTIONS if q in cfg["gated_questions"]]
    pairwise_ok = bool(gated) and all(pw["questions"][q]["passes"] for q in gated)
    status = "insufficient" if not counts_ok else "pass" if pairwise_ok else "fail"

    provisional, notes = [], []
    if len(who) <= 1:
        provisional.append("single labeller")
        notes.append("Single-labeller mode: the human majority is one person, and human-vs-human agreement "
                     "(the ceiling the judge is measured against) cannot be computed. The plan expects the owner "
                     "plus one other person (BRD §1.7); import the second labeller's file and rerun `calibrate`.")
    if manifest.get("mode") != "live":
        provisional.append(f"{manifest.get('mode')} run")
        notes.append(f"This is a {manifest.get('mode')} run: the judge verdicts are not the gate judge's, so this "
                     "calibration does not count toward EVAL-13.")
    if pw["unjudged"] or ab["unjudged"]:
        notes.append(f"{pw['unjudged']} labelled pair(s) and {ab['unjudged']} labelled document(s) have no verdicts "
                     f"for this run's judge versions yet; run `python -m evals judge` on the run.")
    untrusted = [c for c in absolute.CRITERIA if not ab["criteria"][c]["trusted"]]
    return {
        "run_id": store.run_id,
        "mode": manifest.get("mode"),
        "computed_at": now_iso(),
        "judge_version": jv,
        "absolute_version": av,
        "recalibration": recalibration,
        "labellers": who,
        "thresholds": {"pairwise_agreement": float(cfg["pairwise_agreement"]), "absolute_kappa": float(cfg["absolute_kappa"]),
                       "min_pairwise_labels": min_pw, "min_absolute_labels": min_ab, "gated_questions": gated,
                       "bootstrap_resamples": boot["resamples"]},
        "status": status,
        "provisional": provisional,
        "counts_ok": counts_ok,
        "pairwise_ok": pairwise_ok,
        "trusted_criteria": [c for c in absolute.CRITERIA if c not in untrusted],
        "untrusted_criteria": untrusted,
        "pairwise": pw,
        "absolute": ab,
        "notes": notes,
    }


# ---------------------------------------------------------------- rendering


def _f(v: float | None, pct: bool = False) -> str:
    if v is None:
        return "—"
    return f"{v * 100:.1f}%" if pct else f"{v:.2f}"


def _est(e: dict[str, Any], pct: bool = False) -> str:
    if not e or e.get("value") is None:
        return "—"
    ci = e.get("ci")
    return _f(e["value"], pct) + (f" ({_f(ci[0], pct)}–{_f(ci[1], pct)})" if ci else "")


def render_section(s: dict[str, Any]) -> list[str]:
    """Markdown lines for the calibration section of a report (and calibration.md)."""
    t = s["thresholds"]
    status = {"pass": "**PASS**", "fail": "**FAIL**", "insufficient": "**NOT ENOUGH LABELS**"}[s["status"]]
    if s["provisional"]:
        status += f" — provisional ({', '.join(s['provisional'])})"
    pw, ab = s["pairwise"], s["absolute"]
    out = ["## Judge calibration (EVAL-13)", "",
           f"- Status: {status}",
           f"- Judge `{s['judge_version']}`, absolute rubric `{s['absolute_version']}`; computed {s['computed_at']}",
           f"- Labellers: {', '.join(f'`{l}`' for l in s['labellers']) or 'none'}"
           + (" (recalibration: at least " + str(t["min_pairwise_labels"]) + " labels of each kind)" if s["recalibration"] else ""),
           f"- Labels usable (labelled and judged): {pw['usable']} pair(s) (need {t['min_pairwise_labels']}), "
           f"{ab['usable']} document(s) (need {t['min_absolute_labels']})",
           f"- Thresholds: pairwise agreement with the human majority on non-tie items ≥ {_f(t['pairwise_agreement'], True)} "
           f"(gated: {', '.join(pairwise.QUESTION_LABELS[q] for q in t['gated_questions'])}); "
           f"quadratic-weighted κ ≥ {t['absolute_kappa']:.2f} per criterion (below: reported, not gated)",
           ""]
    for n in s["notes"]:
        out += [f"> {n}", ""]

    out += ["### Pairwise: judge vs human majority", "",
            "Agreement on non-tie items (human majority chose a side; a judge tie counts as a disagreement). "
            "Judge verdict = both orders agree, else tie. 95% cluster-bootstrap CIs over cases.", "",
            "| Question | Agreement (non-tie) | Non-tie items | Judge ties there | 3-way agreement | Cohen's κ | Gated | Meets threshold |",
            "|---|---|---|---|---|---|---|---|"]
    for q in pairwise.QUESTIONS:
        r = pw["questions"][q]
        out.append(f"| {pairwise.QUESTION_LABELS[q]} | {_est(r['agreement'], True)} | {r['non_tie_items']} of {r['items']} | "
                   f"{r['judge_ties_on_non_tie']} | {_est(r['raw_agreement'], True)} | {_est(r['kappa'])} | "
                   f"{'yes' if r['gated'] else 'no'} | {'yes' if r['passes'] else '**no**'} |")
    out.append("")
    if len(pw["labellers"]) > 1:
        out += ["Judge agreement (non-tie) against each labeller: "
                + "; ".join(f"`{l}` " + ", ".join(f"{pairwise.QUESTION_LABELS[q]} {_f(pw['questions'][q]['per_labeller'][l], True)}"
                                                 for q in pairwise.QUESTIONS) for l in pw["labellers"]), ""]
        nm = pw["questions"]["overall"]["no_human_majority"]
        out += [f"Items without a human majority (counted as human ties), overall question: {nm}.", ""]

    out += ["### Absolute rubric: judge vs human", "",
            "Pooled over labellers (one point per document and labeller). Mean diff = judge − human.", "",
            "| Criterion | Weighted κ (quadratic) | Exact | Within 1 | Mean diff | Points | Trusted for gating |",
            "|---|---|---|---|---|---|---|"]
    for c in absolute.CRITERIA:
        r = ab["criteria"][c]
        md = "—" if r["mean_diff"] is None else f"{r['mean_diff']:+.2f}"
        out.append(f"| {absolute.CRITERION_LABELS[c]} | {_est(r['kappa'])} | {_f(r['exact'], True)} | {_f(r['within_one'], True)} | "
                   f"{md} | {r['pairs']} | {'yes' if r['trusted'] else '**no** (reported, not gated)'} |")
    out.append("")

    out += ["### Human vs human", ""]
    if not pw["human_vs_human"] and not ab["human_vs_human"]:
        out += ["Not computable: only one labeller. Inter-human agreement (and so the ceiling for the judge) is unknown.", ""]
    for h in pw["human_vs_human"]:
        a, b = h["labellers"]
        out += [f"Pairwise, `{a}` vs `{b}` ({h['items']} common item(s)):", "",
                "| Question | 3-way agreement | Cohen's κ | Agreement where both chose a side |", "|---|---|---|---|"]
        for q in pairwise.QUESTIONS:
            r = h["questions"][q]
            out.append(f"| {pairwise.QUESTION_LABELS[q]} | {_est(r['raw_agreement'], True)} | {_est(r['kappa'])} | "
                       f"{_f(r['both_sided_agreement'], True)} |")
        out.append("")
    for h in ab["human_vs_human"]:
        a, b = h["labellers"]
        out += [f"Absolute, `{a}` vs `{b}` ({h['items']} common document(s)):", "",
                "| Criterion | Weighted κ | Exact | Within 1 |", "|---|---|---|---|"]
        for c in absolute.CRITERIA:
            r = h["criteria"][c]
            out.append(f"| {absolute.CRITERION_LABELS[c]} | {_est(r['kappa'])} | {_f(r['exact'], True)} | {_f(r['within_one'], True)} |")
        out.append("")
    return out


def write_calibration(store: RunStore, summary: dict[str, Any]) -> tuple[str, str]:
    """``calibration.json`` and ``calibration.md`` in the run; the status goes into the manifest."""
    j = store.path / "calibration.json"
    m = store.path / "calibration.md"
    write_atomic(j, json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    write_atomic(m, f"# Calibration of run `{store.run_id}`\n\n" + "\n".join(render_section(summary)) + "\n")
    store.touch_manifest(calibration={k: summary[k] for k in (
        "status", "provisional", "judge_version", "absolute_version", "labellers", "computed_at",
        "trusted_criteria", "untrusted_criteria", "recalibration")})
    return str(j), str(m)


def load_calibration(store: RunStore) -> dict[str, Any] | None:
    p = store.path / "calibration.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
