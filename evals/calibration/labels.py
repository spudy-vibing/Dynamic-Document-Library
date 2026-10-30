"""Importing completed labels (WP-018, EVAL-13).

A label file comes from one labeller for one pack: the JSON the labelling page downloads
(``doccode-labels/1``), or the pack's ``labels.csv`` filled in by hand (the labeller ID is
then given on the command line). Validation, before anything is stored:

- the pack must be one exported from this run (its key is in ``calibration/keys/``);
- every item ID must belong to the pack, with the right kind, at most once;
- pairwise verdicts are ``A``, ``B`` or ``tie`` for all four questions; absolute scores are
  integers 1-5 for all six criteria; a half-filled item counts as missing;
- every item of the pack must be labelled, unless ``allow_missing``;
- a labeller can import into a pack once; ``replace`` swaps their earlier labels.

Stored rows go to ``runs/<id>/calibration/labels.jsonl``, one per (pack, labeller, item),
with the verdicts mapped back through the key: ``winners`` holds, per question, ``"x"``,
``"y"`` or ``"tie"`` for the configured pair (x, y), so labels from different packs and
labellers compare directly with the judge.
"""

from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path
from typing import Any

from evals.calibration.pack import CSV_COLUMNS, LABELS_FORMAT, CalibrationError, load_keys
from evals.core.storage import RUNS_DIR, RunStore, now_iso
from evals.judge import absolute, pairwise

STREAM = "calibration/labels"
LABELLER_RE = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")
VERDICT_ALIASES = {"a": "A", "b": "B", "tie": "tie", "t": "tie", "=": "tie"}


def parse_file(path: Path, labeller: str | None = None) -> dict[str, Any]:
    """Read a labels JSON or CSV into ``{"pack_id", "labeller", "labels": [...]}`` (not yet validated)."""
    path = Path(path)
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".csv":
        return _parse_csv(text, labeller)
    try:
        body = json.loads(text)
    except json.JSONDecodeError as e:
        raise CalibrationError(f"{path.name}: not valid JSON ({e})") from None
    if not isinstance(body, dict) or body.get("format") != LABELS_FORMAT:
        raise CalibrationError(f"{path.name}: not a labels file (expected format {LABELS_FORMAT!r})")
    if labeller and body.get("labeller") and body["labeller"] != labeller:
        raise CalibrationError(
            f"{path.name} was saved by labeller {body['labeller']!r}, but --labeller says {labeller!r}"
        )
    return {"pack_id": body.get("pack_id"), "labeller": labeller or body.get("labeller"),
            "labels": list(body.get("labels") or [])}


def _parse_csv(text: str, labeller: str | None) -> dict[str, Any]:
    if not labeller:
        raise CalibrationError("a CSV label sheet needs --labeller <your-id>")
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        raise CalibrationError("the CSV label sheet has no rows")
    missing_cols = [c for c in ("pack_id", "item_id", "kind") if c not in rows[0]]
    if missing_cols:
        raise CalibrationError(f"the CSV label sheet lacks column(s) {', '.join(missing_cols)}; "
                               f"expected {', '.join(CSV_COLUMNS)}")
    packs = {(r.get("pack_id") or "").strip() for r in rows}
    if len(packs) != 1:
        raise CalibrationError(f"the CSV label sheet mixes packs: {', '.join(sorted(packs))}")
    labels = []
    for r in rows:
        kind = (r.get("kind") or "").strip()
        item: dict[str, Any] = {"item_id": (r.get("item_id") or "").strip(), "kind": kind,
                                "note": (r.get("note") or "").strip()}
        if kind == "pairwise":
            item["verdicts"] = {q: r[q].strip() for q in pairwise.QUESTIONS if (r.get(q) or "").strip()}
        else:
            item["scores"] = {c: r[c].strip() for c in absolute.CRITERIA if (r.get(c) or "").strip()}
        labels.append(item)
    return {"pack_id": packs.pop(), "labeller": labeller, "labels": labels}


def _score(v: Any) -> int | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        n = v
    elif isinstance(v, float) and v.is_integer():
        n = int(v)
    elif isinstance(v, str) and v.strip().isdigit():
        n = int(v.strip())
    else:
        return None
    return n if 1 <= n <= 5 else None


def find_pack(pack_id: str, runs_dir: Path = RUNS_DIR, run_id: str | None = None) -> tuple[RunStore, dict[str, Any]]:
    """The run that exported ``pack_id`` and its key (``run_id`` narrows the search to one run)."""
    roots = [Path(runs_dir) / run_id] if run_id else sorted(p for p in Path(runs_dir).glob("*") if p.is_dir())
    for root in roots:
        store = RunStore(root.name, Path(runs_dir))
        if not store.exists():
            continue
        key = load_keys(store).get(pack_id)
        if key:
            return store, key
    where = f"run {run_id}" if run_id else f"any run under {runs_dir}"
    raise CalibrationError(f"pack {pack_id!r} was not exported from {where}; check you have the right label file "
                           "(wrong pack?) or pass --run-id")


def validate(body: dict[str, Any], key: dict[str, Any], *, allow_missing: bool = False) -> list[dict[str, Any]]:
    """Check a parsed label file against the pack key; return the rows to store.

    Raises CalibrationError naming every problem (wrong pack, unknown or duplicate items,
    bad values, missing items).
    """
    if body.get("pack_id") != key["pack_id"]:
        raise CalibrationError(f"these labels are for pack {body.get('pack_id')!r}, not {key['pack_id']!r} (wrong pack)")
    labeller = body.get("labeller") or ""
    if not LABELLER_RE.match(labeller):
        raise CalibrationError(f"labeller ID {labeller!r} is missing or invalid (letters, digits, _ . -; at most 40)")
    items = key["items"]
    problems: list[str] = []
    seen: set[str] = set()
    rows = []
    for lab in body["labels"]:
        iid = str(lab.get("item_id", ""))
        if iid not in items:
            problems.append(f"{iid or '(blank)'}: not an item of pack {key['pack_id']}")
            continue
        if iid in seen:
            problems.append(f"{iid}: labelled more than once")
            continue
        seen.add(iid)
        k = items[iid]
        if lab.get("kind") and lab["kind"] != k["kind"]:
            problems.append(f"{iid}: kind {lab['kind']!r}, but the item is {k['kind']!r}")
            continue
        base = {"pack_id": key["pack_id"], "labeller": labeller, "item_id": iid, "kind": k["kind"],
                "case_id": k["case_id"], "rep": k["rep"], "note": str(lab.get("note") or "")}
        if k["kind"] == "pairwise":
            given = lab.get("verdicts") or {}
            verdicts = {q: VERDICT_ALIASES.get(str(given[q]).strip().lower()) for q in pairwise.QUESTIONS if q in given
                        and str(given[q]).strip()}
            bad = [q for q, v in verdicts.items() if v is None]
            if bad:
                problems.append(f"{iid}: verdict for {', '.join(bad)} must be A, B or tie")
                continue
            if len(verdicts) < len(pairwise.QUESTIONS):
                seen.discard(iid)  # incomplete: reported as missing
                continue
            winners = {}
            for q, v in verdicts.items():
                gen = k["label_a"] if v == "A" else k["label_b"] if v == "B" else None
                winners[q] = "tie" if gen is None else "x" if gen == k["pair"][0] else "y"
            rows.append({**base, "pair": k["pair"], "verdicts": verdicts, "winners": winners})
        else:
            given = lab.get("scores") or {}
            scores = {c: _score(given[c]) for c in absolute.CRITERIA if c in given and str(given[c]).strip()}
            bad = [c for c, v in scores.items() if v is None]
            if bad:
                problems.append(f"{iid}: score for {', '.join(bad)} must be an integer from 1 to 5")
                continue
            if len(scores) < len(absolute.CRITERIA):
                seen.discard(iid)
                continue
            rows.append({**base, "generator": k["generator"], "scores": scores})
    missing = sorted(set(items) - seen)
    if missing and not allow_missing:
        shown = ", ".join(missing[:10]) + (f" and {len(missing) - 10} more" if len(missing) > 10 else "")
        problems.append(f"{len(missing)} item(s) not (fully) labelled: {shown} (finish them, or pass --allow-missing)")
    if problems:
        raise CalibrationError("label file rejected:\n  - " + "\n  - ".join(problems))
    return rows


def import_labels(
    path: Path, *, labeller: str | None = None, runs_dir: Path = RUNS_DIR, run_id: str | None = None,
    replace: bool = False, allow_missing: bool = False,
) -> tuple[RunStore, list[dict[str, Any]]]:
    """Validate a label file and append its rows to the exporting run's ``calibration/labels.jsonl``."""
    body = parse_file(path, labeller)
    if not body.get("pack_id"):
        raise CalibrationError(f"{Path(path).name}: no pack_id")
    store, key = find_pack(str(body["pack_id"]), runs_dir, run_id)
    rows = validate(body, key, allow_missing=allow_missing)
    if not rows:
        raise CalibrationError(f"{Path(path).name}: no fully labelled item to import")
    who = body["labeller"]
    existing = store.read(STREAM)
    already = [r for r in existing if r["pack_id"] == key["pack_id"] and r["labeller"] == who]
    if already and not replace:
        raise CalibrationError(f"labeller {who!r} already imported {len(already)} label(s) for pack {key['pack_id']}; "
                               "use a different labeller ID for a second person, or --replace to swap the labels")
    ts = now_iso()
    rows = [{**r, "imported_at": ts, "source": Path(path).name} for r in rows]
    if already:
        store.rewrite(STREAM, [r for r in existing if not (r["pack_id"] == key["pack_id"] and r["labeller"] == who)] + rows)
    else:
        store.append(STREAM, rows)
    return store, rows


def read_labels(store: RunStore) -> list[dict[str, Any]]:
    return store.read(STREAM)
