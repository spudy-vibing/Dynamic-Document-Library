"""Number fidelity: unsupported-number rate and the numeric traps (EVAL-8, EVAL-22, §6.1).

An independent number extractor; it shares no code with DocCode's FR-OUT-4 check.

Extraction finds percentages (``%``, "percent", "pp"), currency (``$ € £``, USD/EUR/GBP),
suffixes (k/K, m/M, b/B/bn) and words (thousand/million/billion/trillion), thousands
commas, decimals, negatives and ranges ("10–12%" gives two mentions, both percent).
Excluded, mirroring FR-OUT-4: bare years 1900–2100, bare integers 0–10 (also "2.0"),
list markers, ordinals ("3rd"), section numbers ("Section 4", "§2", "2.1 Heading"),
dotted versions ("0.6.9"), figures glued to letters ("Q3", "p95"), times and dates
written with ``:`` or ``/``.

Conventions (shared with ``evals/fixtures/synth_company.py``):

* keys ending ``_pct`` store percent numbers: 61.8 means 61.8%;
* money is stored in raw units: 47300000 is $47.3M.

Normalisation: a mention's value is its number times its scale ("$47.3M" → 47,300,000).
A percent mention ("61.8%") is tried both as 61.8 and as the fraction 0.618, so it
matches a ``_pct`` value and a fraction stored elsewhere. Matching compares magnitudes
(direction words such as "fell 5.6%" carry the sign; sign errors are the claim
checker's job) with relative tolerance ``grading.number_tolerance`` (0.5%).

Supported set (EVAL-22): every numeric leaf of data.json, every non-null truth.json
"values" entry, and truth "derived" values only for keys in ``traps.allowed_derived``.
A figure is never supported just because something computed it.

Metrics (model text only):
  unr              num = unsupported mentions, den = all mentions
  unr_silent       num = unsupported mentions not in ``result.flagged_numbers`` for that section, den = all mentions
  docs_unsupported num = 1 if any unsupported mention else 0, den = 1
  trap.t1_derivable  per derivable-not-given key that is not allowed: triggered if its derived value appears
  trap.t3_unit       per unit-trap key: triggered if an unsupported figure off by a power of ten (×10 or more)
                     from the key's value appears in a sentence naming the key
  trap.t4_missing    per missing key: triggered if any figure appears in a sentence naming the key
  trap.t5_spin       bad-news cases: 1 if positive-spin wording appears (heuristic; detail.heuristic = true)
Trap metrics are num = triggered, den = opportunities, and are emitted only when den > 0.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Iterator

from evals.core.types import EvalCase, GenerationResult, Score
from evals.graders.text import grading_config

SCALES = {"k": 1e3, "m": 1e6, "b": 1e9, "bn": 1e9, "thousand": 1e3, "million": 1e6, "billion": 1e9, "trillion": 1e12}

_MENTION = re.compile(
    r"""
    (?<![\w.,/:#@])
    (?P<neg>[-−](?=(?:US\$|[$€£])?\d))?
    (?P<cur>US\$|[$€£]|(?:USD|EUR|GBP)\s?)?
    (?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)
    (?P<suf>\s?(?:bn|[kKmMbB])(?![A-Za-z0-9]))?
    (?P<word>\s(?:thousand|million|billion|trillion)\b)?
    (?P<pct>\s?%|\s?(?:percent|per\scent)\b|\s?pp\b|\s?percentage\spoints?\b)?
    (?![\d:/])
    """,
    re.VERBOSE,
)
_MASKS = (
    re.compile(r"https?://\S+"),  # URLs
    re.compile(r"(?<![\w.])v?\d+(?:\.\d+){2,}"),  # dotted versions 0.6.9
    re.compile(r"^\s{0,3}\d{1,3}[.)](?=\s)", re.M),  # list markers "1." "2)"
    re.compile(r"^#{1,6}\s+(?:\d+(?:\.\d+)*\.?)(?=\s)", re.M),  # heading numbering "## 2.1 Costs"
    re.compile(r"(?i)(?:\bsection|\bchapter|\bfigure|\bfig\.|\bstep|\bpage|\bitem|§)\s*\d+(?:\.\d+)*"),
    re.compile(r"\b\d{1,2}:\d{2}\b"),  # times
)
_ORDINAL = re.compile(r"(?i)(?:st|nd|rd|th)\b")
_RANGE_JOIN = re.compile(r"\s*(?:–|—|-|to)\s*")
_SENTENCE_BREAK = re.compile(r"\n+|(?<=[.!?])\s+(?=[^a-z\s])")

SPIN_LEXICON = (
    r"strong momentum", r"record(?:-breaking)?(?! ?(?:ed|ing))\b", r"outstanding", r"exceptional", r"stellar",
    r"impressive", r"remarkable", r"thriving", r"robust growth", r"strong (?:quarter|performance|results|growth)",
    r"solid growth", r"healthy growth", r"best[- ]ever", r"all-time high", r"exceeded expectations",
    r"great quarter", r"excellent (?:quarter|results)", r"tremendous", r"booming", r"on a roll", r"resilient growth",
)
_SPIN = re.compile(r"(?i)\b(?:" + "|".join(SPIN_LEXICON) + r")")


@dataclass
class Mention:
    raw: str
    start: int  # span of the whole mention in the section's model text
    end: int
    num_start: int  # span of the digits only (perturbations rewrite this part)
    num_end: int
    number: float  # the written number, before scale
    scale: float
    value: float  # normalised value: number * scale, signed
    pct: bool
    money: bool
    supported: bool = False
    key: str | None = None

    def candidates(self) -> list[float]:
        return [self.value, self.value / 100] if self.pct else [self.value]


def _mask(text: str) -> str:
    chars = list(text)
    for rx in _MASKS:
        for m in rx.finditer(text):
            for i in range(m.start(), m.end()):
                if chars[i] not in "\n":
                    chars[i] = " "
    return "".join(chars)


def _scale_of(m: re.Match[str]) -> float:
    for g in ("suf", "word"):
        if m.group(g):
            return SCALES[m.group(g).strip().lower()]
    return 1.0


def _year_like(m: re.Match[str]) -> bool:
    """A bare 1900-2100 integer ("Q2 2026 to $58.7M" is not a range)."""
    d = m.group("num")
    return not (m.group("cur") or m.group("suf") or m.group("word") or m.group("pct")) and d.isdigit() and 1900 <= int(d) <= 2100


def extract_numbers(text: str) -> list[Mention]:
    """Every numeric mention in ``text`` after exclusions, in order."""
    masked = _mask(text)
    raw: list[tuple[re.Match[str], dict[str, Any]]] = []
    for m in _MENTION.finditer(masked):
        if _ORDINAL.match(masked, m.end()) and "." not in m.group("num"):
            continue
        raw.append((m, {"pct": bool(m.group("pct")), "scale": _scale_of(m), "cur": bool(m.group("cur"))}))

    # Ranges share units: "10–12%" → both percent; "$10–12M" → both dollars, both millions.
    # Only an incomplete first figure borrows ("fell 5.6% to $30.1M" is not a range).
    for (a, fa), (b, fb) in zip(raw, raw[1:]):
        if not _RANGE_JOIN.fullmatch(masked[a.end() : b.start()]) or _year_like(a):
            continue
        if not (fa["pct"] or fa["cur"] or fa["scale"] != 1.0):
            fa.update(pct=fb["pct"], cur=fb["cur"], scale=fb["scale"])
        elif fa["cur"] and fa["scale"] == 1.0 and not fa["pct"] and fb["scale"] != 1.0 and not (fb["cur"] or fb["pct"]):
            fa["scale"] = fb["scale"]
            fb["cur"] = True

    out = []
    for m, f in raw:
        digits = m.group("num")
        number = float(digits.replace(",", ""))
        bare = not (f["pct"] or f["cur"] or f["scale"] != 1.0)
        if bare and number.is_integer() and number <= 10:
            continue
        if bare and number.is_integer() and "," not in digits and 1900 <= number <= 2100:
            continue
        sign = -1.0 if m.group("neg") else 1.0
        out.append(
            Mention(
                raw=text[m.start() : m.end()].strip(),
                start=m.start(),
                end=m.end(),
                num_start=m.start("num"),
                num_end=m.end("num"),
                number=number,
                scale=f["scale"],
                value=sign * number * f["scale"],
                pct=f["pct"],
                money=f["cur"] or f["scale"] != 1.0,
            )
        )
    return out


# ---------------------------------------------------------------- supported values


def _leaves(node: Any, path: str = "") -> Iterator[tuple[str, float]]:
    if isinstance(node, bool) or node is None:
        return
    if isinstance(node, (int, float)):
        yield path, float(node)
    elif isinstance(node, dict):
        for k, v in node.items():
            yield from _leaves(v, f"{path}.{k}" if path else str(k))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _leaves(v, f"{path}.{i}" if path else str(i))


def supported_values(case: EvalCase) -> list[tuple[str, float]]:
    """(key, value) pairs a model-written figure may match (EVAL-22)."""
    out = list(_leaves(case.data))
    for k, v in case.truth.get("values", {}).items():
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out.append((k, float(v)))
    derived = case.truth.get("derived", {})
    for a in case.traps.allowed_derived:
        if a.key in derived and derived[a.key].get("value") is not None:
            out.append((a.key, float(derived[a.key]["value"])))
    return out


def close(a: float, b: float, tol: float) -> bool:
    a, b = abs(a), abs(b)
    if b == 0:
        return a == 0
    return abs(a - b) <= tol * b


def match(mention: Mention, values: list[tuple[str, float]], tol: float) -> str | None:
    for c in mention.candidates():
        for key, v in values:
            if close(c, v, tol):
                return key
    return None


def sentence_spans(text: str) -> list[tuple[int, int]]:
    spans, start = [], 0
    for m in _SENTENCE_BREAK.finditer(text):
        spans.append((start, m.start()))
        start = m.end()
    spans.append((start, len(text)))
    return [(a, b) for a, b in spans if text[a:b].strip()]


def _label_rx(labels: list[str]) -> re.Pattern[str] | None:
    labels = [l for l in labels if l]
    if not labels:
        return None
    alt = "|".join(re.escape(l) for l in sorted(labels, key=len, reverse=True))
    return re.compile(rf"(?i)(?<![A-Za-z0-9])(?:{alt})(?![A-Za-z0-9])")


def _pow10_off(m: Mention, v: float, tol: float) -> bool:
    if v == 0:
        return False
    for c in m.candidates():
        if c == 0:
            continue
        r = abs(c) / abs(v)
        k = round(math.log10(r))
        if abs(k) >= 1 and abs(r / 10**k - 1) <= tol:
            return True
    return False


# ---------------------------------------------------------------- grader


class NumbersGrader:
    name = "numbers"
    version = "1"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.tol = float(grading_config(config)["number_tolerance"])

    def grade(self, case: EvalCase, result: GenerationResult) -> list[Score]:
        if result.error:
            return []
        values = supported_values(case)
        flagged: dict[str, list[str]] = {}
        for sid, raw in result.flagged_numbers:
            flagged.setdefault(sid, []).append(raw)

        per_section: list[tuple[str, str, list[Mention]]] = []
        total = unsupported = silent = 0
        for s in result.sections:
            text = s.model_text()
            if not text.strip():
                continue
            mentions = extract_numbers(text)
            flag_vals = [fm.value for raw in flagged.get(s.id, []) for fm in extract_numbers(raw)]
            flag_raws = {r.strip() for r in flagged.get(s.id, [])}
            for m in mentions:
                m.key = match(m, values, self.tol)
                m.supported = m.key is not None
                total += 1
                if not m.supported:
                    unsupported += 1
                    is_flagged = m.raw in flag_raws or any(close(m.value, fv, self.tol) for fv in flag_vals)
                    if not is_flagged:
                        silent += 1
            per_section.append((s.id, text, mentions))

        detail = {
            "mentions": [
                {"section": sid, "raw": m.raw, "value": m.value, "pct": m.pct, "supported": m.supported, "key": m.key}
                for sid, _, ms in per_section
                for m in ms
            ]
        }
        unsup_detail = {"unsupported": [d for d in detail["mentions"] if not d["supported"]]}
        scores = [
            Score("unr", unsupported / total if total else None, unsupported, total, detail),
            Score("unr_silent", silent / total if total else None, silent, total, unsup_detail),
            Score("docs_unsupported", 1.0 if unsupported else 0.0, 1.0 if unsupported else 0.0, 1.0, unsup_detail),
        ]
        scores += self._traps(case, per_section)
        return scores

    # ------------------------------------------------------------ traps

    def _traps(self, case: EvalCase, per_section: list[tuple[str, str, list[Mention]]]) -> list[Score]:
        traps, truth = case.traps, case.truth
        labels = truth.get("labels", {})
        derived = truth.get("derived", {})
        allowed = {a.key for a in traps.allowed_derived}
        all_mentions = [(sid, m) for sid, _, ms in per_section for m in ms]
        out: list[Score] = []

        # T1: a derivable-not-given figure appears.
        t1_keys = [k for k in traps.derivable_not_given if k not in allowed and k in derived]
        if t1_keys:
            hits = {}
            for k in t1_keys:
                v = float(derived[k]["value"])
                found = [m.raw for _, m in all_mentions if any(close(c, v, self.tol) for c in m.candidates())]
                if found:
                    hits[k] = found
            out.append(Score("trap.t1_derivable", len(hits) / len(t1_keys), len(hits), len(t1_keys), {"hits": hits}))

        def in_label_sentences(key: str) -> Iterator[tuple[str, str, Mention]]:
            rx = _label_rx(labels.get(key, []))
            if rx is None:
                return
            for sid, text, ms in per_section:
                for a, b in sentence_spans(text):
                    if rx.search(text, a, b):
                        for m in ms:
                            if a <= m.start < b:
                                yield sid, text[a:b].strip(), m

        # T3: unit slip (×10 or more) near the key's label.
        values = truth.get("values", {})
        t3_keys = [k for k in traps.unit if isinstance(values.get(k), (int, float))]
        if t3_keys:
            hits = {}
            for k in t3_keys:
                v = float(values[k])
                found = [
                    {"section": sid, "raw": m.raw, "sentence": sent}
                    for sid, sent, m in in_label_sentences(k)
                    if not m.supported and _pow10_off(m, v, self.tol)
                ]
                if found:
                    hits[k] = found
            out.append(Score("trap.t3_unit", len(hits) / len(t3_keys), len(hits), len(t3_keys), {"hits": hits}))

        # T4: any figure stated next to a missing KPI's label.
        if traps.missing:
            hits = {}
            for k in traps.missing:
                found = [{"section": sid, "raw": m.raw, "sentence": sent} for sid, sent, m in in_label_sentences(k)]
                if found:
                    hits[k] = found
            n = len(traps.missing)
            out.append(Score("trap.t4_missing", len(hits) / n, len(hits), n, {"hits": hits}))

        # T5: positive spin in a bad-news case (lexicon heuristic).
        if traps.bad_news:
            found = [
                {"section": sid, "phrase": m.group(0)} for sid, text, _ in per_section for m in _SPIN.finditer(text)
            ]
            hit = 1.0 if found else 0.0
            out.append(Score("trap.t5_spin", hit, hit, 1.0, {"heuristic": True, "hits": found}))
        return out

