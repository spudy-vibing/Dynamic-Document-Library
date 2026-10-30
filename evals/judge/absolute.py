"""Absolute rubric judge (EVAL-10, EVAL-12, EVALS_PROPOSAL §6.2).

Six criteria, each scored 1–5 against frozen anchors, in three grouped calls per document
(grouping reduces the halo effect; every score cites evidence):

    group a: C1 accuracy vs data,   C2 coherence across sections
    group b: C3 no repetition,      C4 formatting
    group c: C5 tone vs style guide, C6 instruction adherence (semantic items)

Rubric files: ``rubrics/absolute_common.md`` (system, context, document, answer format)
and ``rubrics/absolute_{a,b,c}.md`` (the anchored criteria). The judge sees only the
anonymised, normalised Markdown (the same text as the pairwise judge), the brief, the
outline with constraints, data.json and the attachments.

Used for floors and per-document diagnosis only, never for ranking: reports show the
share of documents scoring at or below ``absolute.floor`` (2) on any criterion and the
documents scoring 1 on C1 (BRD §1.5); per-criterion means are diagnosis, and ``compare``
never flags them as regressions.

Rows: ``absolute.jsonl``, one per (document, group), resumable like ``judge.jsonl``.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Callable, Iterable

from evals.core.llm import FakeLLM, message_text
from evals.core.types import LLM, EvalCase, GenerationResult
from evals.generators.common import PromptFile, load_prompt
from evals.judge.pairwise import RUBRICS_DIR, normalised_markdown, word_count
from evals.judge.perdoc import DocJob, doc_key, latest_ok

STREAM = "absolute"
ROLE = "absolute"
GROUPS: dict[str, tuple[str, str]] = {
    "a": ("c1_accuracy", "c2_coherence"),
    "b": ("c3_repetition", "c4_formatting"),
    "c": ("c5_tone", "c6_instructions"),
}
CRITERIA: tuple[str, ...] = tuple(c for g in GROUPS.values() for c in g)
CRITERION_LABELS = {
    "c1_accuracy": "C1 accuracy vs data",
    "c2_coherence": "C2 coherence",
    "c3_repetition": "C3 no repetition",
    "c4_formatting": "C4 formatting",
    "c5_tone": "C5 tone vs style",
    "c6_instructions": "C6 instruction adherence",
}
COMMON = "absolute_common"
DEFAULT_FLOOR = 2


def rubric_files() -> list[PromptFile]:
    return [load_prompt(COMMON, RUBRICS_DIR)] + [load_prompt(f"absolute_{g}", RUBRICS_DIR) for g in GROUPS]


def absolute_version(config: dict[str, Any]) -> str:
    """config judge.version + one hash over every absolute rubric file (EVAL-12)."""
    h = hashlib.sha256("".join(p.sha for p in rubric_files()).encode()).hexdigest()
    return f"{config['judge']['version']}-abs-{h[:8]}"


def floor_of(config: dict[str, Any]) -> int:
    return int(config.get("absolute", {}).get("floor", DEFAULT_FLOOR))


def enabled(config: dict[str, Any]) -> bool:
    return bool(config.get("absolute", {}).get("enabled", True))


# ---------------------------------------------------------------- prompt


def outline_with_constraints(case: EvalCase) -> str:
    lines = []
    for i, s in enumerate(case.sections, 1):
        c = s.constraints
        limits = [
            f"max {c.max_words} words" if c.max_words else "",
            f"min {c.min_words} words" if c.min_words else "",
            f"{c.paragraphs} paragraph(s)" if c.paragraphs else "",
            f"{c.list_items} list item(s)" if c.list_items else "",
            ("must not use: " + ", ".join(f'"{p}"' for p in c.forbidden_phrases)) if c.forbidden_phrases else "",
        ]
        limits = [x for x in limits if x]
        line = f"{i}. {s.title}: {s.brief}. Instruction: {s.instruction}"
        if limits:
            line += " Constraints: " + "; ".join(limits) + "."
        lines.append(line)
    return "\n".join(lines)


def attachments_text(case: EvalCase) -> str:
    if not case.attachments:
        return "(none)"
    return "\n".join(
        f'<attachment name="{name}">\n{case.attachments[name].rstrip()}\n</attachment>' for name in sorted(case.attachments)
    )


def answer_shape(criteria: Iterable[str]) -> str:
    return json.dumps({c: {"evidence": ['"..."'], "issues": ["..."], "score": 3} for c in criteria}, ensure_ascii=False)


def build_request(case: EvalCase, document: str, group: str) -> tuple[str, list[dict[str, Any]]]:
    """System = shared rules; user = [context (cached per case), document (cached per doc), criteria + answer]."""
    common = load_prompt(COMMON, RUBRICS_DIR)
    crit = load_prompt(f"absolute_{group}", RUBRICS_DIR)
    context = common.render(
        "context",
        title=case.title,
        purpose=case.purpose,
        audience=case.audience or "-",
        style=case.style or "-",
        outline=outline_with_constraints(case),
        data_json=json.dumps(case.data, indent=2, ensure_ascii=False),
        attachments=attachments_text(case),
    )
    doc = common.render("document", document=document.strip())
    ask = crit.render("criteria") + "\n\n" + common.render("answer", shape=answer_shape(GROUPS[group]))
    content = [
        {"type": "text", "text": context, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": doc, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": ask},
    ]
    return common.render("system"), [{"role": "user", "content": content}]


# ---------------------------------------------------------------- parsing


def _score(v: Any) -> int | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)) and float(v).is_integer():
        n = int(v)
    elif isinstance(v, str) and v.strip().isdigit():
        n = int(v.strip())
    else:
        return None
    return n if 1 <= n <= 5 else None


def parse_scores(text: str, criteria: Iterable[str]) -> dict[str, dict[str, Any]]:
    """{criterion: {"score": 1..5, "evidence": [...], "issues": [...]}} from the judge's JSON reply.

    Raises ValueError when no JSON object holds every requested criterion with an integer
    score from 1 to 5 and a list of evidence (evidence before the score is the rule).
    """
    criteria = tuple(criteria)
    candidates = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])
    problem = "no JSON object in judge reply"
    for raw in candidates:
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        out: dict[str, dict[str, Any]] = {}
        for c in criteria:
            item = obj.get(c)
            if not isinstance(item, dict):
                problem = f"criterion {c} missing"
                break
            score = _score(item.get("score"))
            if score is None:
                problem = f"criterion {c}: score {item.get('score')!r} is not an integer from 1 to 5"
                break
            evidence = item.get("evidence")
            if not isinstance(evidence, list):
                problem = f"criterion {c}: evidence must be a list of quotes"
                break
            issues = item.get("issues", item.get("contradictions", []))
            out[c] = {"score": score, "evidence": [str(e) for e in evidence],
                      "issues": [str(i) for i in issues] if isinstance(issues, list) else [str(issues)]}
        else:
            return out
    raise ValueError(problem)


# ---------------------------------------------------------------- jobs


def custom_id(result: GenerationResult, group: str) -> str:
    return f"a{doc_key(result.generator, result.case_id, result.rep)}-{group}"


def plan_jobs(
    results: Iterable[GenerationResult], cases: dict[str, EvalCase], done: set[str]
) -> tuple[list[DocJob], int]:
    """One job per (error-free document, group) not already done. Returns (jobs, skipped docs)."""
    jobs, skipped = [], 0
    for r in sorted(results, key=lambda r: (r.generator, r.case_id, r.rep)):
        if r.error or r.case_id not in cases:
            skipped += 1
            continue
        case = cases[r.case_id]
        doc = normalised_markdown(r)
        for g, crit in GROUPS.items():
            cid = custom_id(r, g)
            if cid in done:
                continue
            system, messages = build_request(case, doc, g)
            jobs.append(DocJob(cid, case, r, system, messages,
                               parse=lambda text, crit=crit: {"scores": parse_scores(text, crit)},
                               fields={"group": g, "words": word_count(doc)}))
    return jobs, skipped


# ---------------------------------------------------------------- scores


def doc_scores(rows: Iterable[dict[str, Any]], version: str) -> dict[tuple[str, str, int], dict[str, int]]:
    """(generator, case_id, rep) -> {criterion: score} from the latest ok row of each group."""
    out: dict[tuple[str, str, int], dict[str, int]] = {}
    for r in latest_ok(rows, version).values():
        slot = out.setdefault((r["generator"], r["case_id"], r["rep"]), {})
        for c, item in (r.get("scores") or {}).items():
            slot[c] = int(item["score"])
    return out


def score_rows(rows: Iterable[dict[str, Any]], version: str, *, floor: int = DEFAULT_FLOOR) -> list[dict[str, Any]]:
    """Score-shaped rows (like scores.jsonl) for report and compare.

    ``abs.<criterion>``: the score (mean in reports; diagnosis only).
    ``abs_floor``: 1 if any criterion is at or below ``floor``; emitted when one is, or when
    all six criteria are scored (an incomplete document cannot pass the floor).
    ``abs_c1_is_1``: 1 if C1 accuracy is 1; emitted when C1 is scored.
    """
    out = []
    for (g, case_id, rep), scores in sorted(doc_scores(rows, version).items()):
        base = {"generator": g, "case_id": case_id, "rep": rep, "grader": STREAM, "grader_version": version}
        for c in CRITERIA:
            if c in scores:
                out.append({**base, "metric": f"abs.{c}", "value": float(scores[c]), "num": None, "den": None, "detail": {}})
        low = sorted(c for c, s in scores.items() if s <= floor)
        if low or all(c in scores for c in CRITERIA):
            hit = 1.0 if low else 0.0
            out.append({**base, "metric": "abs_floor", "value": hit, "num": hit, "den": 1.0, "detail": {"low": low}})
        if "c1_accuracy" in scores:
            hit = 1.0 if scores["c1_accuracy"] == 1 else 0.0
            out.append({**base, "metric": "abs_c1_is_1", "value": hit, "num": hit, "den": 1.0, "detail": {}})
    return out


# ---------------------------------------------------------------- fake judge

_CRIT_IN_ASK = re.compile(r"\((c\d_[a-z]+), score 1")
_SENT = re.compile(r"(?<=[.!?])\s+")


def _body(text: str) -> str:
    m = re.search(r"<document>\n?(.*?)</document>", text, re.S)
    return m.group(1) if m else ""


def fake_absolute_responder(role: str, node_id: str | None, system: str, messages: list[dict[str, Any]]) -> str:
    """Deterministic stand-in: structural heuristics for C3/C4, a document hash for the rest (3 or 4)."""
    content = messages[-1]["content"]
    text = message_text(content)
    ask = content[-1]["text"] if isinstance(content, list) else text
    criteria = _CRIT_IN_ASK.findall(ask) or list(CRITERIA)
    doc = _body(text)
    prose = [p for p in doc.split("\n\n") if p.strip() and not p.lstrip().startswith(("#", "|", "*"))]
    first = _SENT.split(prose[0].strip())[0] if prose else ""
    sentences = [s.strip().lower() for p in prose for s in _SENT.split(p) if len(s.split()) >= 6]
    share = (len(sentences) - len(set(sentences))) / len(sentences) if sentences else 0.0
    titles = sum(1 for line in doc.splitlines() if line.startswith("# "))
    h = int(hashlib.sha256(doc.encode()).hexdigest(), 16)
    scores = {
        "c3_repetition": 4 if share < 0.15 else 3 if share < 0.4 else 2,
        "c4_formatting": 4 if titles <= 1 and not re.search(r"(?im)^(sure|certainly|here is|here's)\b", doc) else 2,
    }
    reply = {}
    for i, c in enumerate(criteria):
        s = scores.get(c, 3 + ((h >> i) & 1))
        reply[c] = {"evidence": [f'"{first}"'] if first else [], "issues": [] if s >= 3 else [f"fake: {c} heuristic"], "score": s}
    return json.dumps(reply)


def make_fake_judge(config: dict[str, Any]) -> Callable[[], LLM]:
    model, effort = config["models"]["judge"], config["effort"]["judge"]
    return lambda: FakeLLM(model, effort, config["max_tokens"]["judge"], config, responder=fake_absolute_responder)
