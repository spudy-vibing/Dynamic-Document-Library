"""Blinded labelling packs (WP-018, EVAL-13, EVALS_PROPOSAL §6.3).

``export_pack`` samples, from one run, the pairs and documents the humans label:

- **Pairs** (default 40): the run's configured judge pairs (``judge.pairs`` with both
  generators in the run; B0/B1/B2/P* on dev cases), stratified by generator pair, then spread
  over families and cases, with seeded randomness. A pair slot is one (case, rep, x, y) with
  two error-free documents; it does not need judge verdicts yet, so humans can label before
  (or without seeing) the judge.
- **Documents** (default 20): error-free documents stratified by generator, then spread over
  families and cases.

The pack shows exactly what the judges see: the case brief, outline (with constraints), data
and attachments, and the anonymised, normalised Markdown of each document
(``pairwise.normalised_markdown``). No generator names, case IDs or judge output appear in
it. Items have random IDs, appear in random order (pairs first, then documents), and each
pair's A/B sides are drawn at random, independently of the judge's orders.

The mapping back (item -> generator, case, rep, A/B sides) is the pack **key**, kept only in
the run directory (``calibration/keys/<pack_id>.json``), never in the exported folder.

Written to the pack folder (default ``runs/<id>/calibration/packs/<pack_id>/``):

    pack.html    single-file offline labelling page; saves a labels JSON to download
    pack.json    the pack (machine-readable; the HTML embeds the same data)
    pack.md      the pack as Markdown, for labelling into labels.csv without a browser
    labels.csv   blank label sheet, one row per item (the CSV fallback)

Packs are only exported from runs on an allowed case set (``calibration.allowed_case_sets``,
default dev): held-out outputs never go to labellers or builders (EVALS_PROPOSAL §5.3).
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import random
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, TypeVar

from evals.core.cases import load_case
from evals.core.storage import RunStore, now_iso, resolve_case_path, write_atomic
from evals.core.types import EvalCase, GenerationResult
from evals.generators.common import load_prompt
from evals.judge import absolute, pairwise
from evals.judge.pairwise import RUBRICS_DIR

PACK_FORMAT = "doccode-label-pack/1"
LABELS_FORMAT = "doccode-labels/1"
TEMPLATE = Path(__file__).resolve().parent / "labeller.html"
CSV_COLUMNS = ("pack_id", "item_id", "kind", *pairwise.QUESTIONS, *absolute.CRITERIA, "note")

#: ``[calibration]`` defaults (EVALS_PROPOSAL §6.3, BRD EVAL-13); config.toml overrides them.
DEFAULTS: dict[str, Any] = {
    "seed": 20261030,
    "pairs": 40,
    "documents": 20,
    "allowed_case_sets": ["dev"],
    "exclude_generators": ["n0-null"],
    "min_pairwise_labels": 40,
    "min_absolute_labels": 20,
    "recalibration_min_labels": 10,
    "pairwise_agreement": 0.75,
    "absolute_kappa": 0.6,
    "gated_questions": ["overall"],
    "bootstrap_resamples": 10_000,
}

T = TypeVar("T")


class CalibrationError(ValueError):
    """A labelling pack or label file that cannot be used; the message says why and what to do."""


def settings(config: dict[str, Any]) -> dict[str, Any]:
    """``[calibration]`` from config.toml over the defaults."""
    return {**DEFAULTS, **config.get("calibration", {})}


# ---------------------------------------------------------------- paths


def calibration_dir(store: RunStore) -> Path:
    return store.path / "calibration"


def key_path(store: RunStore, pack_id: str) -> Path:
    return calibration_dir(store) / "keys" / f"{pack_id}.json"


def default_pack_dir(store: RunStore, pack_id: str) -> Path:
    return calibration_dir(store) / "packs" / pack_id


def load_keys(store: RunStore) -> dict[str, dict[str, Any]]:
    """pack_id -> key, for every pack exported from this run."""
    out = {}
    for p in sorted((calibration_dir(store) / "keys").glob("*.json")):
        key = json.loads(p.read_text(encoding="utf-8"))
        out[key["pack_id"]] = key
    return out


# ---------------------------------------------------------------- sampling


def stratified(
    strata: dict[Any, list[T]], n: int, rng: random.Random, case_of: Callable[[T], str], family_of: Callable[[T], str]
) -> list[T]:
    """Up to ``n`` items: one per stratum in turn, each pick spreading the sample over cases and families.

    Strata are visited in a seeded random order. Within a stratum the pick is the candidate
    whose case has been picked least so far (over the whole sample), then whose family has,
    then the first in a seeded shuffle. So every stratum gets an equal share (+-1), and cases
    and families are covered as evenly as the run allows.
    """
    keys = sorted(strata, key=str)
    rng.shuffle(keys)
    pools = {}
    for k in keys:
        pool = list(strata[k])
        rng.shuffle(pool)
        pools[k] = pool
    per_case: Counter[str] = Counter()
    per_family: Counter[str] = Counter()
    out: list[T] = []
    while len(out) < n and any(pools.values()):
        for k in keys:
            pool = pools[k]
            if len(out) >= n or not pool:
                continue
            best = min(range(len(pool)), key=lambda i: (per_case[case_of(pool[i])], per_family[family_of(pool[i])], i))
            pick = pool.pop(best)
            per_case[case_of(pick)] += 1
            per_family[family_of(pick)] += 1
            out.append(pick)
    return out


@dataclass(frozen=True)
class PairSlot:
    case_id: str
    rep: int
    x: str
    y: str


@dataclass(frozen=True)
class DocSlot:
    generator: str
    case_id: str
    rep: int


def sample(
    results: Iterable[GenerationResult],
    pairs: Iterable[Iterable[str]],
    families: dict[str, str],
    *,
    n_pairs: int,
    n_docs: int,
    seed: int | str,
    exclude: Iterable[str] = (),
) -> tuple[list[PairSlot], list[DocSlot]]:
    """Stratified, seeded sample of pair slots (by generator pair) and documents (by generator)."""
    excluded = set(exclude)
    ok = {(r.generator, r.case_id, r.rep) for r in results if not r.error and r.generator not in excluded}
    slots = sorted({(c, rep) for _, c, rep in ok})
    fam = lambda case_id: families.get(case_id, "unknown")  # noqa: E731

    pair_strata: dict[tuple[str, str], list[PairSlot]] = {}
    for x, y in (tuple(p) for p in pairs):
        found = [PairSlot(c, rep, x, y) for c, rep in slots if (x, c, rep) in ok and (y, c, rep) in ok]
        if found:
            pair_strata[(x, y)] = found
    chosen_pairs = stratified(pair_strata, n_pairs, random.Random(f"calibration-pairs|{seed}"),
                              lambda s: s.case_id, lambda s: fam(s.case_id))

    doc_strata: dict[str, list[DocSlot]] = {}
    for g, c, rep in sorted(ok):
        doc_strata.setdefault(g, []).append(DocSlot(g, c, rep))
    chosen_docs = stratified(doc_strata, n_docs, random.Random(f"calibration-docs|{seed}"),
                             lambda s: s.case_id, lambda s: fam(s.case_id))
    return chosen_pairs, chosen_docs


# ---------------------------------------------------------------- labeller instructions


def _questions() -> list[dict[str, str]]:
    """The pairwise questions, worded exactly as in the judge rubric."""
    text = load_prompt(pairwise.RUBRIC, RUBRICS_DIR).parts["documents"]
    out = []
    for q in pairwise.QUESTIONS:
        m = re.search(rf"^- {q}: (.+)$", text, re.M)
        out.append({"id": q, "label": pairwise.QUESTION_LABELS[q], "text": m.group(1).strip() if m else q})
    return out


def _criteria() -> list[dict[str, str]]:
    """The six absolute criteria with their anchors, worded exactly as in the judge rubric."""
    out = []
    for g, crits in absolute.GROUPS.items():
        text = load_prompt(f"absolute_{g}", RUBRICS_DIR).parts["criteria"]
        blocks = re.split(r"(?m)^(?=## Criterion )", text)
        for c in crits:
            body = next((b for b in blocks if f"({c}," in b), "")
            head, _, rest = body.partition("\n")
            out.append({"id": c, "label": absolute.CRITERION_LABELS[c],
                        "title": head.removeprefix("## ").strip(), "text": rest.strip()})
    return out


def _rules() -> dict[str, str]:
    pw = load_prompt(pairwise.RUBRIC, RUBRICS_DIR).parts["system"]
    ab = load_prompt(absolute.COMMON, RUBRICS_DIR).parts["system"]

    def bullets(text: str) -> str:
        return "\n".join(line for line in text.splitlines() if line.startswith("- "))

    return {"pairwise": bullets(pw), "absolute": bullets(ab)}


def case_context(case: EvalCase) -> dict[str, str]:
    """What the judges see of the case (absolute's superset: outline with constraints, attachments)."""
    return {
        "title": case.title,
        "purpose": case.purpose,
        "audience": case.audience or "-",
        "style": case.style or "-",
        "outline": absolute.outline_with_constraints(case),
        "data": json.dumps(case.data, indent=2, ensure_ascii=False),
        "attachments": absolute.attachments_text(case),
    }


# ---------------------------------------------------------------- building


def _cases(manifest: dict[str, Any]) -> dict[str, EvalCase]:
    out = {}
    for ref in manifest.get("case_paths") or []:
        case = load_case(resolve_case_path(ref))
        out[case.id] = case
    return out


def check_exportable(manifest: dict[str, Any], config: dict[str, Any]) -> None:
    allowed = list(settings(config)["allowed_case_sets"])
    case_set = manifest.get("case_set")
    if case_set not in allowed:
        raise CalibrationError(
            f"run {manifest.get('run_id')} is on case set {case_set!r}; labelling packs are only exported from "
            f"{', '.join(allowed)} runs (held-out outputs never leave the owner, EVALS_PROPOSAL §5.3)"
        )


def build_pack(
    store: RunStore, config: dict[str, Any], *, seed: int | str | None = None,
    n_pairs: int | None = None, n_docs: int | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """(pack, key) for ``store``. Deterministic for the same run contents, seed and sizes."""
    cfg = settings(config)
    manifest = store.read_manifest()
    check_exportable(manifest, config)
    seed = cfg["seed"] if seed is None else seed
    n_pairs = int(cfg["pairs"] if n_pairs is None else n_pairs)
    n_docs = int(cfg["documents"] if n_docs is None else n_docs)
    cases = _cases(manifest)
    results = {(r.generator, r.case_id, r.rep): r for r in store.iter_results() if r.case_id in cases}
    names = {g["name"] for g in manifest.get("generators", [])}
    pairs = [p for p in config["judge"]["pairs"] if p[0] in names and p[1] in names]
    families = {cid: c.family for cid, c in cases.items()}
    pair_slots, doc_slots = sample(results.values(), pairs, families, n_pairs=n_pairs, n_docs=n_docs, seed=seed,
                                   exclude=cfg["exclude_generators"])
    if not pair_slots and not doc_slots:
        raise CalibrationError(f"run {store.run_id} has no error-free documents to label")

    digest = hashlib.sha1(json.dumps(
        [store.run_id, str(seed), n_pairs, n_docs, [vars(s) for s in pair_slots], [vars(s) for s in doc_slots]]
    ).encode()).hexdigest()
    pack_id = f"pk-{digest[:10]}"
    rng = random.Random(f"calibration-pack|{seed}|{pack_id}")
    used_ids: set[str] = set()

    def new_id() -> str:
        while True:
            i = f"item-{rng.getrandbits(32):08x}"
            if i not in used_ids:
                used_ids.add(i)
                return i

    contexts: dict[str, str] = {}

    def ctx(case_id: str) -> str:
        return contexts.setdefault(case_id, f"ctx-{len(contexts) + 1}")

    items: list[dict[str, Any]] = []
    key_items: dict[str, dict[str, Any]] = {}
    pair_items, doc_items = [], []
    for s in pair_slots:
        a, b = (s.x, s.y) if rng.random() < 0.5 else (s.y, s.x)
        iid = new_id()
        pair_items.append({"item_id": iid, "kind": "pairwise", "context": ctx(s.case_id),
                           "doc_a": pairwise.normalised_markdown(results[(a, s.case_id, s.rep)]).strip(),
                           "doc_b": pairwise.normalised_markdown(results[(b, s.case_id, s.rep)]).strip()})
        key_items[iid] = {"kind": "pairwise", "case_id": s.case_id, "rep": s.rep, "pair": [s.x, s.y],
                          "label_a": a, "label_b": b}
    for s in doc_slots:
        iid = new_id()
        doc_items.append({"item_id": iid, "kind": "absolute", "context": ctx(s.case_id),
                          "doc": pairwise.normalised_markdown(results[(s.generator, s.case_id, s.rep)]).strip()})
        key_items[iid] = {"kind": "absolute", "generator": s.generator, "case_id": s.case_id, "rep": s.rep}
    rng.shuffle(pair_items)
    rng.shuffle(doc_items)
    items = pair_items + doc_items
    for n, it in enumerate(items, 1):
        it["n"] = n

    pack = {
        "format": PACK_FORMAT,
        "pack_id": pack_id,
        "run_id": store.run_id,
        "mode": manifest.get("mode"),
        "created_at": now_iso(),
        "questions": _questions(),
        "criteria": _criteria(),
        "rules": _rules(),
        "contexts": {cid: case_context(cases[case_id]) for case_id, cid in contexts.items()},
        "items": items,
    }
    key = {
        "pack_id": pack_id,
        "run_id": store.run_id,
        "seed": str(seed),
        "created_at": pack["created_at"],
        "judge_version": manifest.get("judge_version"),
        "absolute_version": manifest.get("absolute_version"),
        "items": key_items,
    }
    return pack, key


# ---------------------------------------------------------------- writing


def pack_json(pack: dict[str, Any]) -> str:
    return json.dumps(pack, indent=2, ensure_ascii=False) + "\n"


def pack_html(pack: dict[str, Any]) -> str:
    """The offline labelling page: the template with the pack embedded as JSON."""
    data = json.dumps(pack, ensure_ascii=False).replace("<", "\\u003c")  # no "</script>" or "<!--" in the page
    return TEMPLATE.read_text(encoding="utf-8").replace("__PACK_JSON__", data)


def _fence(text: str) -> str:
    ticks = "`" * max(4, max((len(m) for m in re.findall(r"`+", text)), default=0) + 1)
    return f"{ticks}markdown\n{text}\n{ticks}"


def pack_markdown(pack: dict[str, Any]) -> str:
    """The pack for reading in any editor; labels go into labels.csv (the CSV fallback)."""
    out = [f"# Labelling pack `{pack['pack_id']}`", ""]
    if pack.get("mode") != "live":
        out += [f"> Run mode: **{pack.get('mode')}**. Not a real result unless the mode is live.", ""]
    out += [
        "Fill in `labels.csv` (one row per item; leave the columns of the other kind empty) and import it with",
        f"`python -m evals label import labels.csv --labeller <your-id>`.",
        "",
        "- **Pairwise items**: for each question answer `A`, `B` or `tie`.",
        "- **Document items**: score each criterion with an integer from 1 to 5, using the anchors literally.",
        "",
        "## Pairwise questions", "",
        *[f"- `{q['id']}` ({q['label']}): {q['text']}" for q in pack["questions"]],
        "", "Rules the judge follows (apply them too):", "", pack["rules"]["pairwise"], "",
        "## Absolute criteria", "", pack["rules"]["absolute"], "",
    ]
    for c in pack["criteria"]:
        out += [f"### `{c['id']}`: {c['title']}", "", c["text"], ""]
    out += ["## Case contexts", ""]
    for cid, c in pack["contexts"].items():
        out += [f"### {cid}: {c['title']}", "", f"- Purpose: {c['purpose']}", f"- Audience: {c['audience']}",
                f"- Style: {c['style']}", "", "Outline:", "", c["outline"], "", "Data:", "", "```json", c["data"], "```",
                "", "Attachments:", "", _fence(c["attachments"]), ""]
    out += ["## Items", ""]
    for it in pack["items"]:
        if it["kind"] == "pairwise":
            out += [f"### {it['n']}. `{it['item_id']}` — pairwise (context {it['context']})", "",
                    "#### Document A", "", _fence(it["doc_a"]), "", "#### Document B", "", _fence(it["doc_b"]), ""]
        else:
            out += [f"### {it['n']}. `{it['item_id']}` — document (context {it['context']})", "", _fence(it["doc"]), ""]
    return "\n".join(out)


def labels_csv_template(pack: dict[str, Any]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(CSV_COLUMNS)
    for it in pack["items"]:
        w.writerow([pack["pack_id"], it["item_id"], it["kind"]] + [""] * (len(CSV_COLUMNS) - 3))
    return buf.getvalue()


def export_pack(
    store: RunStore, config: dict[str, Any], *, out_dir: Path | None = None, seed: int | str | None = None,
    n_pairs: int | None = None, n_docs: int | None = None,
) -> tuple[dict[str, Any], Path]:
    """Build the pack, save its key in the run, write the pack folder. Returns (pack, folder)."""
    pack, key = build_pack(store, config, seed=seed, n_pairs=n_pairs, n_docs=n_docs)
    out = Path(out_dir) if out_dir else default_pack_dir(store, pack["pack_id"])
    if out.resolve() == key_path(store, pack["pack_id"]).parent.resolve():
        raise CalibrationError("the pack folder must not be the folder that holds the pack keys")
    write_atomic(key_path(store, pack["pack_id"]), json.dumps(key, indent=2, ensure_ascii=False) + "\n")
    write_atomic(out / "pack.json", pack_json(pack))
    write_atomic(out / "pack.html", pack_html(pack))
    write_atomic(out / "pack.md", pack_markdown(pack))
    write_atomic(out / "labels.csv", labels_csv_template(pack))
    return pack, out
