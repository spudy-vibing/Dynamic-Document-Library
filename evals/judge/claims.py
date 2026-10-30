"""Claim checker: silent misattributed claims (EVAL-11, EVALS_PROPOSAL §6.2, BRD §1.5 "Data fidelity").

Model-assisted extraction, deterministic verification:

1. **Extraction (model).** ``models.claims`` (Sonnet) at ``effort.claims`` reads the model-written
   prose of every section and returns every quantitative or comparative claim as
   ``{section, text, metric, value, entity, period, direction, verdict, reason}``, with
   ``metric`` chosen from the words (label, entity, period), never from the value.
   Prompt: ``rubrics/claims.md``. One call per document; Batches when live.
2. **Verification (deterministic, ``verify``).** Wherever the claim names a ground-truth key
   with a figure or a direction, the harness recomputes the verdict from truth.json:
   the figure must match that key (or a derived change of it) within
   ``grading.number_tolerance``; a figure matching a different key is ``misattributed``
   (T2, and team/entity swaps), or ``wrong_period`` when that key is the same metric in
   another period (T6); a stated direction must equal the truth's (``facts.<x>_direction``
   or the sign of a derived period-over-period change), else ``wrong_direction``. Only
   claims the harness cannot check (facts, unmapped metrics) keep the model's verdict.
   Rows record both verdicts, so model/rule agreement measures the extractor.

``rule_claims`` is a deterministic, label-based extractor with the same output shape. It
drives the FAKE responder (so offline runs exercise the whole path) and the perturbation
tests; it is a heuristic and never replaces the model in a live run.

Metrics (per document; misattributed = misattributed + wrong_period + wrong_direction):
  misattrib              num = misattributed claims,           den = claims extracted
  misattrib_silent       num = misattributed claims not flagged by the generator, den = claims
  docs_misattrib_silent  num = 1 if any silent misattributed claim, den = 1   (gate: <= 1% of docs)
A claim is flagged when the generator flagged a number in that section with the claim's
value (``GenerationResult.flagged_numbers``); for baselines every claim is silent.

This module never imports ``doccode`` (EVAL-8).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import cached_property
from typing import Any, Callable, Iterable

from evals.core.llm import FakeLLM
from evals.core.types import LLM, EvalCase, GenerationResult
from evals.generators.common import load_prompt
from evals.graders.numbers import close, extract_numbers, sentence_spans
from evals.graders.text import grading_config
from evals.judge.pairwise import RUBRICS_DIR
from evals.judge.perdoc import DocJob, doc_key, latest_ok

STREAM = "claims"
ROLE = "claims"
RUBRIC = "claims"
VERDICTS = ("correct", "misattributed", "wrong_period", "wrong_direction", "wrong_value", "unquantified", "unverifiable")
MISATTRIB = frozenset({"misattributed", "wrong_period", "wrong_direction"})
DIRECTIONS = ("up", "down", "flat")

PERIOD_TOKENS = frozenset({"prior", "this", "current", "prev", "previous", "last"})
GENERIC_TOKENS = frozenset({"pct", "pp", "change", "total", "the", "and"})
_UP = r"rose|rise|rises|risen|rising|grew|grow|grows|growing|increased?|increasing|up|gained?|climbed?|higher|jumped?|expanded?"
_DOWN = r"fell|fall|falls|falling|declined?|declining|decreased?|decreasing|down|dropped?|lower|shrank|shrunk|contracted?|slipped?|eased?"
_FLAT = r"flat|unchanged|steady|stable|held"
_DIRECTION = re.compile(rf"(?i)\b(?:(?P<up>{_UP})|(?P<down>{_DOWN})|(?P<flat>{_FLAT}))\b")
_KEY_REF = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def rubric():
    return load_prompt(RUBRIC, RUBRICS_DIR)


def claims_version(config: dict[str, Any]) -> str:
    """config claims.version + the prompt file's hash (EVAL-12)."""
    return f"{config['claims']['version']}-{rubric().sha[:8]}"


def enabled(config: dict[str, Any]) -> bool:
    return bool(config.get("claims", {}).get("enabled", True))


# ---------------------------------------------------------------- ground truth


def period_family(key: str) -> str:
    """``revenue_prior`` and ``revenue_this`` -> ``revenue``; ``churn_prior_pct`` -> ``churn_pct``."""
    return "_".join(t for t in key.split("_") if t not in PERIOD_TOKENS)


@dataclass
class Truth:
    """truth.json, indexed for checking claims."""

    case: EvalCase
    tol: float

    @cached_property
    def values(self) -> dict[str, float | None]:
        out: dict[str, float | None] = {}
        for k, v in self.case.truth.get("values", {}).items():
            out[k] = float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None
        return out

    @cached_property
    def derived(self) -> dict[str, tuple[float, list[str]]]:
        """key -> (value, truth keys its formula references, in order)."""
        out = {}
        for k, d in self.case.truth.get("derived", {}).items():
            if isinstance(d, dict) and isinstance(d.get("value"), (int, float)):
                refs = [t for t in _KEY_REF.findall(str(d.get("formula", ""))) if t in self.values]
                out[k] = (float(d["value"]), refs)
        return out

    @cached_property
    def facts(self) -> dict[str, Any]:
        return dict(self.case.truth.get("facts", {}))

    @cached_property
    def known(self) -> list[tuple[str, float]]:
        """Every truth figure a claim may carry: values (non-null) and every derived value."""
        out = [(k, v) for k, v in self.values.items() if v is not None]
        return out + [(k, v) for k, (v, _) in self.derived.items()]

    def value_of(self, key: str) -> float | None:
        if key in self.values:
            return self.values[key]
        return self.derived[key][0] if key in self.derived else None

    def is_key(self, key: str | None) -> bool:
        return bool(key) and (key in self.values or key in self.derived)

    def counterparts(self, key: str) -> set[str]:
        """The same metric in another period (``revenue_prior`` for ``revenue_this``), incl. T6 trap pairs."""
        fam = period_family(key)
        out = {k for k in self.values if k != key and period_family(k) == fam}
        for a, b in self.case.traps.period_confusion:
            if key == a:
                out.add(b)
            elif key == b:
                out.add(a)
        return out

    def related(self, key: str) -> set[str]:
        """``key``, its period counterparts and every derived figure computed from them."""
        base = {key} | self.counterparts(key)
        if key in self.derived:
            base |= set(self.derived[key][1])
        out = set(base)
        for k, (_, refs) in self.derived.items():
            if base & set(refs):
                out.add(k)
        return out

    def direction(self, key: str) -> str | None:
        """Truth direction of a metric: facts ``<prefix>_direction``, else a derived period change's sign."""
        for fk, fv in self.facts.items():
            if fk.endswith("_direction") and str(fv) in DIRECTIONS:
                prefix = fk[: -len("_direction")]
                if key == prefix or key.startswith(prefix + "_"):
                    return str(fv)
        if key in self.derived:
            changes = [key]
        else:
            mine = {key} | self.counterparts(key)
            changes = [k for k, (_, refs) in self.derived.items() if mine & set(refs)]
        for k in changes:
            v, refs = self.derived[k]
            if len(set(refs)) < 2 or len({period_family(r) for r in refs}) != 1:
                continue  # not a period-over-period change of one metric (e.g. a sum across teams)
            if set(refs[0].split("_")) & {"prior", "prev", "previous", "last"}:
                v = -v  # formula written prior-first
            return "up" if v > 0 else "down" if v < 0 else "flat"
        return None


# ---------------------------------------------------------------- verification


def claim_candidates(claim: dict[str, Any]) -> list[float]:
    """The figure(s) a claim carries: its ``value``, else every number written in its text."""
    v = claim.get("value")
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        out = [float(v)]
        if str(claim.get("metric") or "").endswith("_pct") and abs(v) < 1:
            out.append(float(v) * 100)  # a fraction for a percent-stored key
        return out
    return [c for m in extract_numbers(str(claim.get("text", ""))) for c in m.candidates()]


def verify(truth: Truth, claim: dict[str, Any]) -> tuple[str | None, str]:
    """(verdict, reason) recomputed from truth.json, or (None, reason) when it cannot be checked."""
    metric = claim.get("metric")
    if not truth.is_key(metric):
        why = "a fact, not a figure" if metric in truth.facts else "no ground-truth key named"
        return None, why
    cands = claim_candidates(claim)
    direction = claim.get("direction") if claim.get("direction") in DIRECTIONS else None
    if cands:
        related = truth.related(metric)
        ok = [k for k in sorted(related - truth.counterparts(metric))
              if truth.value_of(k) is not None and any(close(c, truth.value_of(k), truth.tol) for c in cands)]
        if not ok:
            others = [k for k, v in truth.known if k not in ok and any(close(c, v, truth.tol) for c in cands)]
            period = [k for k in others if k in truth.counterparts(metric)]
            if period:
                return "wrong_period", f"{metric}: the figure is {period[0]} (another period)"
            wrong = [k for k in others if k not in related]
            if wrong:
                return "misattributed", f"{metric}: the figure is {wrong[0]}"
            if truth.value_of(metric) is None:
                return "wrong_value", f"{metric} has no value in the data"
            return "wrong_value", f"{metric} is {truth.value_of(metric):g}; the figure matches nothing"
    truth_dir = truth.direction(metric)
    if direction and truth_dir and direction != truth_dir:
        return "wrong_direction", f"{metric} went {truth_dir}, the claim says {direction}"
    if not cands:
        if direction and truth_dir:
            return "unquantified", f"{metric} went {truth_dir}; no figure given"
        return None, "no figure and no checkable direction"
    return "correct", f"{metric} matches"


def check_claims(case: EvalCase, claims: list[dict[str, Any]], tol: float) -> list[dict[str, Any]]:
    """Final verdict per claim: the deterministic one where it applies, else the model's."""
    truth = Truth(case, tol)
    out = []
    for c in claims:
        rule, reason = verify(truth, c)
        model = c.get("verdict") if c.get("verdict") in VERDICTS else "unverifiable"
        out.append({**c, "model_verdict": model, "verdict": rule or model, "checked_by": "rule" if rule else "model",
                    "rule_reason": reason})
    return out


def doc_metrics(result: GenerationResult, claims: list[dict[str, Any]], tol: float) -> dict[str, dict[str, Any]]:
    flagged: dict[str, list[float]] = {}
    for sid, raw in result.flagged_numbers:
        flagged.setdefault(sid, []).extend(c for m in extract_numbers(raw) for c in m.candidates())
    bad = [c for c in claims if c["verdict"] in MISATTRIB]

    def is_flagged(c: dict[str, Any]) -> bool:
        vals = flagged.get(str(c.get("section")), [])
        return any(close(x, f, tol) for x in claim_candidates(c) for f in vals)

    silent = [c for c in bad if not is_flagged(c)]
    n = len(claims)
    counts = {v: sum(1 for c in claims if c["verdict"] == v) for v in VERDICTS}
    return {
        "misattrib": {"value": len(bad) / n if n else None, "num": len(bad), "den": n, "detail": {"verdicts": counts}},
        "misattrib_silent": {"value": len(silent) / n if n else None, "num": len(silent), "den": n,
                             "detail": {"silent": [c["text"] for c in silent]}},
        "docs_misattrib_silent": {"value": 1.0 if silent else 0.0, "num": 1.0 if silent else 0.0, "den": 1.0,
                                  "detail": {}},
    }


# ---------------------------------------------------------------- deterministic extractor


def _label_index(case: EvalCase) -> tuple[re.Pattern[str] | None, dict[str, list[str]]]:
    by_label: dict[str, list[str]] = {}
    for key, labels in case.truth.get("labels", {}).items():
        for lb in labels or []:
            if lb:
                by_label.setdefault(lb.lower(), []).append(key)
    if not by_label:
        return None, {}
    alt = "|".join(re.escape(lb) for lb in sorted(by_label, key=len, reverse=True))
    return re.compile(rf"(?i)(?<![A-Za-z0-9])(?:{alt})(?![A-Za-z0-9])"), by_label


def _words(text: str) -> set[str]:
    return {w.lower() for w in re.findall(r"[A-Za-z]+", text)}


def _narrow(keys: list[str], label: str, sentence: str, title: str) -> list[str]:
    """Prefer the keys whose own tokens (entity, period) the sentence, then the section title, names."""
    label_words = _words(label)
    for context in (_words(sentence), _words(title)):
        score = {k: sum(1 for t in set(k.split("_")) if t in context and t not in label_words
                        and t not in GENERIC_TOKENS and len(t) > 2) for k in keys}
        best = max(score.values(), default=0)
        if best and list(score.values()).count(best) < len(keys):
            return [k for k in keys if score[k] == best]
    return keys


def _pick(truth: Truth, keys: list[str], cands: list[float]) -> str:
    """The key a figure is attributed to: a candidate (or a derived change of one) it matches, else the current-period one."""
    for k in keys:
        for r in sorted(truth.related(k) - truth.counterparts(k)):
            v = truth.value_of(r)
            if v is not None and any(close(c, v, truth.tol) for c in cands):
                return r
    current = [k for k in keys if not set(k.split("_")) & {"prior", "prev", "previous", "last"}]
    return (current or keys)[0]


def rule_claims(case: EvalCase, result: GenerationResult, tol: float = 0.005) -> list[dict[str, Any]]:
    """Label-based claims in model-written prose: one per figure next to a truth label, one per direction word."""
    rx, by_label = _label_index(case)
    if rx is None:
        return []
    truth = Truth(case, tol)
    out = []
    for s in result.sections:
        text = s.model_text()
        if not text.strip():
            continue
        mentions = extract_numbers(text)
        for a, b in sentence_spans(text):
            sentence = text[a:b].strip()
            labels = [(m.start(), m.end(), m.group(0)) for m in rx.finditer(text, a, b)]
            if not labels:
                continue

            def nearest(pos: int) -> tuple[int, int, str]:
                before = [lb for lb in labels if lb[1] <= pos]
                return before[-1] if before else min(labels, key=lambda lb: abs(lb[0] - pos))

            claims_here: dict[int, dict[str, Any]] = {}
            for m in (m for m in mentions if a <= m.start < b):
                start, _, label = nearest(m.start)
                keys = _narrow(by_label[label.lower()], label, sentence, s.title)
                cands = m.candidates()
                claim = {"section": s.id, "text": sentence, "metric": _pick(truth, keys, cands), "value": m.value,
                         "entity": None, "period": None, "direction": None}
                claims_here.setdefault(start, claim)
                if claims_here[start] is not claim:
                    out.append(claim)
            for d in _DIRECTION.finditer(text, a, b):
                start, _, label = nearest(d.start())
                direction = next(k for k in DIRECTIONS if d.group(k))
                if start in claims_here:
                    claims_here[start]["direction"] = claims_here[start]["direction"] or direction
                else:
                    keys = _narrow(by_label[label.lower()], label, sentence, s.title)
                    claims_here[start] = {"section": s.id, "text": sentence, "metric": _pick(truth, keys, []),
                                          "value": None, "entity": None, "period": None, "direction": direction}
            out.extend(claims_here.values())
    for c in out:
        verdict, reason = verify(truth, c)
        c["verdict"], c["reason"] = verdict or "unverifiable", reason
    return out


# ---------------------------------------------------------------- prompt and parsing


def sections_text(result: GenerationResult) -> str:
    """Model-written prose only, per section (tables are exact and not checked)."""
    parts = []
    for s in result.sections:
        text = s.model_text().strip()
        if text:
            parts.append(f'<section id="{s.id}" title="{s.title}">\n{text}\n</section>')
    return "\n\n".join(parts) or "(no prose)"


def build_request(case: EvalCase, result: GenerationResult) -> tuple[str, list[dict[str, Any]]]:
    from evals.judge.absolute import attachments_text

    r = rubric()
    truth = {k: case.truth.get(k, {}) for k in ("values", "labels", "derived", "facts")}
    context = r.render(
        "context",
        title=case.title,
        data_json=json.dumps(case.data, indent=2, ensure_ascii=False),
        truth_json=json.dumps(truth, indent=2, ensure_ascii=False),
        attachments=attachments_text(case),
    )
    content = [
        {"type": "text", "text": context, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": r.render("document", sections=sections_text(result))},
    ]
    return r.render("system"), [{"role": "user", "content": content}]


def _num(v: Any) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace(",", "").strip())
    except ValueError:
        return None


def parse_claims(text: str) -> list[dict[str, Any]]:
    """The ``claims`` list from the checker's JSON reply. Raises ValueError when there is none."""
    candidates = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])
    for raw in candidates:
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        items = obj.get("claims") if isinstance(obj, dict) else None
        if not isinstance(items, list):
            continue
        out = []
        for it in items:
            if not isinstance(it, dict) or not str(it.get("text", "")).strip():
                raise ValueError("every claim needs a quoted text")
            verdict = str(it.get("verdict", "")).strip().lower().replace(" ", "_")
            direction = str(it.get("direction") or "").strip().lower() or None
            out.append({
                "section": it.get("section"),
                "text": str(it["text"]),
                "metric": it.get("metric") or None,
                "value": _num(it.get("value")),
                "entity": it.get("entity"),
                "period": it.get("period"),
                "direction": direction if direction in DIRECTIONS else None,
                "verdict": verdict if verdict in VERDICTS else "unverifiable",
                "reason": str(it.get("reason", "")),
            })
        return out
    raise ValueError("no claims JSON in checker reply")


# ---------------------------------------------------------------- jobs and scores


def custom_id(result: GenerationResult) -> str:
    return f"c{doc_key(result.generator, result.case_id, result.rep)}"


def plan_jobs(
    results: Iterable[GenerationResult], cases: dict[str, EvalCase], done: set[str], config: dict[str, Any]
) -> tuple[list[DocJob], int]:
    tol = float(grading_config(config)["number_tolerance"])
    jobs, skipped = [], 0
    for r in sorted(results, key=lambda r: (r.generator, r.case_id, r.rep)):
        if r.error or r.case_id not in cases:
            skipped += 1
            continue
        cid = custom_id(r)
        if cid in done:
            continue
        case = cases[r.case_id]

        def parse(text: str, case: EvalCase = case, r: GenerationResult = r) -> dict[str, Any]:
            claims = check_claims(case, parse_claims(text), tol)
            checked = [c for c in claims if c["checked_by"] == "rule"]
            agree = sum(1 for c in checked if c["model_verdict"] == c["verdict"])
            return {"claims": claims, "scores": doc_metrics(r, claims, tol),
                    "rule_checked": len(checked), "model_agrees": agree}

        system, messages = build_request(case, r)
        jobs.append(DocJob(cid, case, r, system, messages, parse=parse))
    return jobs, skipped


def score_rows(rows: Iterable[dict[str, Any]], version: str) -> list[dict[str, Any]]:
    """Score-shaped rows (like scores.jsonl) from the latest ok claims row per document."""
    out = []
    for r in sorted(latest_ok(rows, version).values(), key=lambda r: (r["generator"], r["case_id"], r["rep"])):
        base = {"generator": r["generator"], "case_id": r["case_id"], "rep": r["rep"], "grader": STREAM,
                "grader_version": version}
        for metric, s in (r.get("scores") or {}).items():
            out.append({**base, "metric": metric, "value": s.get("value"), "num": s.get("num"), "den": s.get("den"),
                        "detail": s.get("detail", {})})
    return out


# ---------------------------------------------------------------- fake checker


def make_fake_checker(config: dict[str, Any], jobs: list[DocJob]) -> Callable[[], LLM]:
    """FAKE mode: the 'model' is ``rule_claims`` on the job's document (looked up by custom_id)."""
    tol = float(grading_config(config)["number_tolerance"])
    by_id = {j.custom_id: j for j in jobs}

    def responder(role: str, node_id: str | None, system: str, messages: list[dict[str, Any]]) -> str:
        job = by_id.get(node_id or "")
        claims = rule_claims(job.case, job.result, tol) if job else []
        return json.dumps({"claims": claims})

    model, effort = config["models"]["claims"], config["effort"]["claims"]
    return lambda: FakeLLM(model, effort, config["max_tokens"]["claims"], config, responder=responder)
