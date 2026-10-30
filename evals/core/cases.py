"""Load case directories: case.toml + data.json + truth.json (+ attachments)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from evals.core.config import EVALS_DIR
from evals.core.types import (
    AllowedDerived,
    CaseSection,
    Constraints,
    DataBlockSpec,
    EvalCase,
    Traps,
)

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

CASES_DIR = EVALS_DIR / "cases"
SPEC_VERSION = 1


class CaseError(ValueError):
    pass


def _pairs(raw: Any) -> tuple[tuple[str, str], ...]:
    return tuple((str(a), str(b)) for a, b in (raw or ()))


def load_case(case_dir: Path) -> EvalCase:
    case_dir = Path(case_dir)
    with open(case_dir / "case.toml", "rb") as f:
        raw = tomllib.load(f)

    if raw.get("spec_version") != SPEC_VERSION:
        raise CaseError(f"{case_dir}: spec_version must be {SPEC_VERSION}")
    data = json.loads((case_dir / raw.get("data", "data.json")).read_text())
    truth = json.loads((case_dir / raw.get("truth", "truth.json")).read_text())

    sections = []
    for s in raw.get("sections", []):
        c = s.get("constraints", {})
        sections.append(
            CaseSection(
                id=s["id"],
                title=s["title"],
                brief=s["brief"],
                instruction=s["instruction"],
                depends_on=tuple(s.get("depends_on", ())),
                constraints=Constraints(
                    max_words=c.get("max_words"),
                    min_words=c.get("min_words"),
                    paragraphs=c.get("paragraphs"),
                    list_items=c.get("list_items"),
                    forbidden_phrases=tuple(c.get("forbidden_phrases", ())),
                ),
                required_facts=tuple(s.get("required_facts", ())),
                data_blocks=tuple(
                    DataBlockSpec(
                        kind=b["kind"], key=b["key"], label=b.get("label"), caption=b.get("caption")
                    )
                    for b in s.get("data_blocks", ())
                ),
            )
        )

    t = raw.get("traps", {})
    traps = Traps(
        derivable_not_given=tuple(t.get("derivable_not_given", ())),
        near_collision=_pairs(t.get("near_collision")),
        unit=tuple(t.get("unit", ())),
        missing=tuple(t.get("missing", ())),
        bad_news=bool(t.get("bad_news", False)),
        period_confusion=_pairs(t.get("period_confusion")),
        injected_instruction=t.get("injected_instruction"),
        allowed_derived=tuple(
            AllowedDerived(key=a["key"], formula=a["formula"]) for a in t.get("allowed_derived", ())
        ),
    )

    attachments = {
        name: (case_dir / rel).read_text() for name, rel in raw.get("attachments", {}).items()
    }

    case = EvalCase(
        id=raw["id"],
        family=raw["family"],
        spec_version=raw["spec_version"],
        title=raw["title"],
        purpose=raw["purpose"],
        audience=raw.get("audience", ""),
        style=raw.get("style", ""),
        sections=tuple(sections),
        data=data,
        truth=truth,
        traps=traps,
        attachments=attachments,
        path=case_dir,
    )
    validate_case(case)
    return case


def validate_case(case: EvalCase) -> None:
    ids = [s.id for s in case.sections]
    if len(ids) != len(set(ids)):
        raise CaseError(f"{case.id}: duplicate section ids")
    for s in case.sections:
        for dep in s.depends_on:
            if dep not in ids or ids.index(dep) >= ids.index(s.id):
                raise CaseError(f"{case.id}: section {s.id} depends on {dep}, which must come earlier")
        for b in s.data_blocks:
            resolve_key(case.data, b.key)  # raises if absent
        for fact in s.required_facts:
            if fact not in case.truth.get("values", {}) and fact not in case.truth.get("facts", {}):
                raise CaseError(f"{case.id}: required fact {fact} not in truth.json")
    for key in case.traps.missing:
        if case.truth.get("values", {}).get(key, "absent") is not None:
            raise CaseError(f"{case.id}: missing-value trap {key} must be null in truth.json values")


def resolve_key(data: Any, key: str) -> Any:
    node = data
    for part in key.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
        else:
            raise CaseError(f"data key not found: {key}")
    return node


def load_case_set(name: str) -> list[EvalCase]:
    root = CASES_DIR / name
    return [load_case(p.parent) for p in sorted(root.glob("*/*/case.toml"))]
