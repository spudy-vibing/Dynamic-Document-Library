#!/usr/bin/env python3
"""Requirement traceability (DEVELOPMENT_PLAN §6).

Parses every requirement defined in the BRD (``**FR-REF-1** MUST:``, ``**NFR-6** MUST:``,
``**EVAL-8** MUST:``), maps each to the waves and WPs that schedule it (the ``Req`` column of
the plan's wave tables, plus ``requirements`` in ``.orchestrator/wp/*.toml``), and finds the
tests that cite it by name (``test_fr_ref_1_...``, ``test_nfr_6_...``, ``test_eval_8_...``)
under ``tests/`` and ``evals/``.

It fails (exit 1) when:

* a requirement ID is defined twice in the BRD;
* an ID cited in the BRD, the plan, a manifest or a test name is not defined in the BRD
  (a dangling ID);
* a BRD requirement is never referenced by the plan;
* a MUST scheduled in a completed wave has no citing test. Completed waves are listed in
  ``scripts/traceability.toml`` (``completed_waves``), which the orchestrator updates at each
  gate.

Ranges are expanded: ``FR-CNT-1..3``, ``NFR-4..6``, ``EVAL-1, 2, 8`` and ``FR-REF-2/4/9``.

``--write docs/traceability.md`` writes the report table (CI uploads it; it is never edited
by hand).

Example::

    python scripts/traceability.py
    python scripts/traceability.py --write docs/traceability.md
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised on Python 3.10 only
    import tomli as tomllib

BRD_PATH = "docs/DocCode BRD Write Documents Like Code.md"
PLAN_PATH = "docs/DEVELOPMENT_PLAN.md"
MANIFEST_DIR = ".orchestrator/wp"
CONFIG_PATH = "scripts/traceability.toml"
TEST_ROOTS = ("tests", "evals")

ID_BODY = r"(?:FR-[A-Z]+|NFR|EVAL)-\d+"
DEFINITION_RE = re.compile(rf"\*\*(?P<id>{ID_BODY})\*\*\s+(?P<level>MUST|SHOULD|MAY)\b")
# An ID, then any number of continuations: "..N" / en-dash N (range), ", N" / "/N" (list).
EN_DASH = chr(0x2013)  # ranges are sometimes written with an en dash
REFERENCE_RE = re.compile(
    r"\b(?P<prefix>(?:FR-[A-Z]+|NFR|EVAL)-)(?P<first>\d+)"
    r"(?P<rest>(?:(?:\s*(?:\.\.|" + EN_DASH + r")\s*|,\s*|/)\d+\b(?!-|\.\d))*)"
)
CONTINUATION_RE = re.compile(r"(\.\.|" + EN_DASH + r"|,|/)\s*(\d+)")
WAVE_HEADING_RE = re.compile(r"^###\s+Wave\s+(?P<wave>\d+)\b")
WP_ROW_RE = re.compile(r"^\|\s*(?P<wp>WP-[0-9A-Za-z]+)\s*\|")
TEST_DEF_RE = re.compile(r"\bdef\s+(test_[A-Za-z0-9_]+)\s*\(")
TEST_ID_RE = re.compile(r"(?:^|_)(fr_[a-z]+_\d+|nfr_\d+|eval_\d+)(?=_|$)")


@dataclass
class Requirement:
    """One BRD requirement and what the plan, manifests and tests say about it."""

    id: str
    level: str
    waves: set[int] = field(default_factory=set)
    wps: set[str] = field(default_factory=set)
    tests: set[str] = field(default_factory=set)
    in_plan: bool = False

    @property
    def status(self) -> str:
        """``tested``, ``planned`` (scheduled to a WP) or ``unscheduled``."""
        if self.tests:
            return "tested"
        return "planned" if self.wps else "unscheduled"


@dataclass
class Report:
    """The traceability result. ``ok`` is False when any failure list is non-empty."""

    requirements: dict[str, Requirement]
    duplicates: list[str]
    dangling: dict[str, set[str]]
    unreferenced: list[str]
    untested_due: list[str]
    completed_waves: tuple[int, ...]

    @property
    def ok(self) -> bool:
        """Whether the check passes."""
        return not (self.duplicates or self.dangling or self.unreferenced or self.untested_due)


def sort_key(req_id: str) -> tuple[int, str, int]:
    """Order FR, then NFR, then EVAL, by number.

    >>> sorted(["EVAL-2", "NFR-10", "FR-DOC-2", "NFR-9"], key=sort_key)
    ['FR-DOC-2', 'NFR-9', 'NFR-10', 'EVAL-2']
    """
    prefix, _, num = req_id.rpartition("-")
    family = 0 if prefix.startswith("FR-") else 1 if prefix == "NFR" else 2
    return (family, prefix, int(num))


def extract_ids(text: str) -> list[str]:
    """Return every requirement ID cited in ``text``, with ranges and lists expanded.

    >>> extract_ids("FR-CNT-1..3, FR-RND-1 and NFR-4..6")
    ['FR-CNT-1', 'FR-CNT-2', 'FR-CNT-3', 'FR-RND-1', 'NFR-4', 'NFR-5', 'NFR-6']
    >>> extract_ids("EVAL-1, 2, 8 and FR-REF-2/4/9")
    ['EVAL-1', 'EVAL-2', 'EVAL-8', 'FR-REF-2', 'FR-REF-4', 'FR-REF-9']
    >>> extract_ids("EVAL-<n> and FR-* are placeholders")
    []
    """
    ids: list[str] = []
    for match in REFERENCE_RE.finditer(text):
        prefix = match.group("prefix")
        last = int(match.group("first"))
        ids.append(f"{prefix}{last}")
        for sep, num_text in CONTINUATION_RE.findall(match.group("rest")):
            num = int(num_text)
            if sep in ("..", EN_DASH) and num > last:
                ids.extend(f"{prefix}{n}" for n in range(last + 1, num + 1))
            else:
                ids.append(f"{prefix}{num}")
            last = num
    return ids


def parse_brd(text: str) -> tuple[dict[str, Requirement], list[str]]:
    """Return the defined requirements and any IDs defined more than once."""
    reqs: dict[str, Requirement] = {}
    duplicates: list[str] = []
    for match in DEFINITION_RE.finditer(text):
        req_id = match.group("id")
        if req_id in reqs:
            duplicates.append(req_id)
        else:
            reqs[req_id] = Requirement(req_id, match.group("level"))
    return reqs, duplicates


def split_row(line: str) -> list[str]:
    """Split a Markdown table row on unescaped ``|``.

    >>> split_row(r"| WP-1 | a \\| b | NFR-1 |")
    ['WP-1', 'a \\\\| b', 'NFR-1']
    """
    cells = re.split(r"(?<!\\)\|", line.strip())
    return [c.strip() for c in cells[1:-1]]


def parse_plan(text: str) -> dict[str, set[tuple[int, str]]]:
    """Map each ID in a wave table's ``Req`` column to its (wave, WP) pairs."""
    schedule: dict[str, set[tuple[int, str]]] = {}
    wave: int | None = None
    req_col: int | None = None
    for line in text.splitlines():
        heading = WAVE_HEADING_RE.match(line)
        if heading:
            wave, req_col = int(heading.group("wave")), None
            continue
        if line.startswith("## "):
            wave, req_col = None, None
            continue
        if wave is None or not line.startswith("|"):
            continue
        cells = split_row(line)
        if cells and cells[0] == "WP" and "Req" in cells:
            req_col = cells.index("Req")
            continue
        row = WP_ROW_RE.match(line)
        if row and req_col is not None and req_col < len(cells):
            for req_id in extract_ids(cells[req_col]):
                schedule.setdefault(req_id, set()).add((wave, row.group("wp")))
    return schedule


def load_manifests(directory: Path) -> dict[str, set[tuple[int, str]]]:
    """Map each ID in a manifest's ``requirements`` to its (wave, WP) pairs."""
    schedule: dict[str, set[tuple[int, str]]] = {}
    if not directory.is_dir():
        return schedule
    for path in sorted(directory.glob("*.toml")):
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        wp, wave = str(data.get("id", path.stem)), int(data.get("wave", -1))
        for item in data.get("requirements", []):
            for req_id in extract_ids(str(item)):
                schedule.setdefault(req_id, set()).add((wave, wp))
    return schedule


def requirement_ids_in_test_name(name: str) -> list[str]:
    """Return the IDs a test name cites, normalised to BRD form.

    >>> requirement_ids_in_test_name("test_fr_ref_1_fstring_creates_edge")
    ['FR-REF-1']
    >>> requirement_ids_in_test_name("test_eval_8_grader_never_imports_doccode")
    ['EVAL-8']
    >>> requirement_ids_in_test_name("test_nfr_6_and_nfr_3_imports")
    ['NFR-6', 'NFR-3']
    >>> requirement_ids_in_test_name("test_evaluate_runner")
    []
    """
    return [m.upper().replace("_", "-") for m in TEST_ID_RE.findall(name.removeprefix("test"))]


def scan_tests(roots: Iterable[Path], repo: Path) -> dict[str, set[str]]:
    """Map each cited ID to ``path::test_name`` strings for every test under ``roots``."""
    found: dict[str, set[str]] = {}
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.py")):
            if any(part in {".venv", "__pycache__", "node_modules"} for part in path.parts):
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            rel = path.relative_to(repo).as_posix() if path.is_relative_to(repo) else str(path)
            for name in TEST_DEF_RE.findall(text):
                for req_id in requirement_ids_in_test_name(name):
                    found.setdefault(req_id, set()).add(f"{rel}::{name}")
    return found


def build_report(
    *,
    brd_text: str,
    plan_text: str,
    manifest_schedule: dict[str, set[tuple[int, str]]],
    tests: dict[str, set[str]],
    completed_waves: Sequence[int],
) -> Report:
    """Combine the sources and compute every failure list."""
    reqs, duplicates = parse_brd(brd_text)
    dangling: dict[str, set[str]] = {}

    def note_dangling(ids: Iterable[str], source: str) -> None:
        for req_id in ids:
            if req_id not in reqs:
                dangling.setdefault(req_id, set()).add(source)

    note_dangling(extract_ids(brd_text), "BRD")
    plan_ids = set(extract_ids(plan_text))
    note_dangling(plan_ids, "plan")
    note_dangling(manifest_schedule, "manifests")
    for req_id, names in tests.items():
        note_dangling([req_id], f"tests ({sorted(names)[0]})")

    for source in (parse_plan(plan_text), manifest_schedule):
        for req_id, pairs in source.items():
            if req_id in reqs:
                reqs[req_id].waves |= {w for w, _ in pairs}
                reqs[req_id].wps |= {wp for _, wp in pairs}
    for req_id, names in tests.items():
        if req_id in reqs:
            reqs[req_id].tests |= names
    for req_id in plan_ids & reqs.keys():
        reqs[req_id].in_plan = True

    done = set(completed_waves)
    untested_due = [
        r.id for r in reqs.values() if r.level == "MUST" and r.waves & done and not r.tests
    ]
    return Report(
        requirements=dict(sorted(reqs.items(), key=lambda kv: sort_key(kv[0]))),
        duplicates=sorted(set(duplicates), key=sort_key),
        dangling=dict(sorted(dangling.items(), key=lambda kv: sort_key(kv[0]))),
        unreferenced=sorted((r.id for r in reqs.values() if not r.in_plan), key=sort_key),
        untested_due=sorted(untested_due, key=sort_key),
        completed_waves=tuple(sorted(done)),
    )


def render_markdown(report: Report) -> str:
    """Render ``docs/traceability.md``."""
    counts: dict[str, int] = {}
    for req in report.requirements.values():
        counts[req.status] = counts.get(req.status, 0) + 1
    summary = ", ".join(f"{n} {status}" for status, n in sorted(counts.items()))
    lines = [
        "# Requirement traceability",
        "",
        "Generated by `scripts/traceability.py` (DEVELOPMENT_PLAN §6). Do not edit by hand.",
        "",
        f"{len(report.requirements)} requirements: {summary}. "
        f"Completed waves: {', '.join(map(str, report.completed_waves)) or 'none'}.",
        "",
        "| Requirement ID | Level | Wave | WP | Tests | Status |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for req in report.requirements.values():
        waves = ", ".join(str(w) for w in sorted(req.waves) if w >= 0) or "—"
        wps = ", ".join(sorted(req.wps)) or "—"
        tests = "<br>".join(f"`{t}`" for t in sorted(req.tests)) or "—"
        lines.append(f"| {req.id} | {req.level} | {waves} | {wps} | {tests} | {req.status} |")
    return "\n".join(lines) + "\n"


def print_summary(report: Report) -> None:
    """Print the failures, or a one-line OK."""
    total = len(report.requirements)
    tested = sum(1 for r in report.requirements.values() if r.tests)
    print(f"traceability: {total} BRD requirements, {tested} with a citing test.")
    if report.duplicates:
        print(f"FAIL defined more than once in the BRD: {', '.join(report.duplicates)}")
    for req_id, sources in report.dangling.items():
        print(
            f"FAIL dangling ID {req_id} (not defined in the BRD), "
            f"cited in: {', '.join(sorted(sources))}"
        )
    if report.unreferenced:
        print(f"FAIL never referenced by the plan: {', '.join(report.unreferenced)}")
    if report.untested_due:
        print(
            "FAIL MUSTs scheduled in a completed wave with no citing test: "
            + ", ".join(report.untested_due)
        )
    if report.ok:
        print("traceability: OK.")


def main(argv: Sequence[str] | None = None) -> int:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(description="Requirement traceability (plan §6).")
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--brd", default=BRD_PATH)
    parser.add_argument("--plan", default=PLAN_PATH)
    parser.add_argument("--manifests", default=MANIFEST_DIR)
    parser.add_argument("--config", default=CONFIG_PATH)
    parser.add_argument("--write", metavar="PATH", help="write the Markdown report here")
    args = parser.parse_args(argv)
    repo: Path = args.repo.resolve()

    config_path = repo / args.config
    config = tomllib.loads(config_path.read_text(encoding="utf-8")) if config_path.is_file() else {}
    report = build_report(
        brd_text=(repo / args.brd).read_text(encoding="utf-8"),
        plan_text=(repo / args.plan).read_text(encoding="utf-8"),
        manifest_schedule=load_manifests(repo / args.manifests),
        tests=scan_tests([repo / r for r in TEST_ROOTS], repo),
        completed_waves=[int(w) for w in config.get("completed_waves", [])],
    )
    if args.write:
        out = repo / args.write
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render_markdown(report), encoding="utf-8")
        print(f"traceability: wrote {args.write}")
    print_summary(report)
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
