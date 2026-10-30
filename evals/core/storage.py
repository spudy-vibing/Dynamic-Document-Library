"""Run directories, manifest, JSONL files and resume (EVAL-15, EVALS_PROPOSAL §8).

Layout of ``evals/runs/<run_id>/`` (gitignored)::

    manifest.json
    docs/<generator>/<case>/<rep>.md      normalised Markdown
    docs/<generator>/<case>/<rep>.json    the full GenerationResult
    calls.jsonl    one row per CallRecord (phase "generate" or "judge")
    errors.jsonl   infra errors and refusals; never scored
    scores.jsonl   one row per (generator, case, rep, metric)
    judge.jsonl    one row per judged order of a pair
    report.md
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

from evals.core.config import EVALS_DIR
from evals.core.types import BlockOut, CallRecord, EvalCase, GenerationResult, SectionOut

RUNS_DIR = EVALS_DIR / "runs"
SCOREBOARD_DIR = EVALS_DIR / "scoreboard"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def git_sha(cwd: Path = EVALS_DIR.parent) -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=cwd, capture_output=True, text=True, timeout=5, check=True
        )
        return out.stdout.strip() or None
    except Exception:  # noqa: BLE001 - not a repo, git missing, ...
        return None


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def case_set_hash(cases: Iterable[EvalCase]) -> str:
    """Hash of every file in every case directory (or of the in-memory case when it has none)."""
    h = hashlib.sha256()
    for case in sorted(cases, key=lambda c: c.id):
        h.update(case.id.encode())
        root = Path(case.path)
        files = sorted(p for p in root.rglob("*") if p.is_file()) if root.is_dir() else []
        if files:
            for p in files:
                h.update(str(p.relative_to(root)).encode())
                h.update(p.read_bytes())
        else:
            h.update(json.dumps([case.data, case.truth], sort_keys=True).encode())
    return h.hexdigest()


def case_path_ref(case: EvalCase) -> str:
    """Case directory relative to evals/ when possible, so manifests are portable."""
    p = Path(case.path).resolve()
    try:
        return str(p.relative_to(EVALS_DIR))
    except ValueError:
        return str(p)


def resolve_case_path(ref: str) -> Path:
    p = Path(ref)
    return p if p.is_absolute() else EVALS_DIR / p


# ---------------------------------------------------------------- (de)serialisation


def result_to_dict(result: GenerationResult) -> dict[str, Any]:
    return asdict(result)


def result_from_dict(d: dict[str, Any]) -> GenerationResult:
    sections = [
        SectionOut(id=s["id"], title=s["title"], level=s["level"], blocks=[BlockOut(**b) for b in s["blocks"]])
        for s in d.get("sections", [])
    ]
    calls = [CallRecord(**c) for c in d.get("calls", [])]
    return GenerationResult(
        generator=d["generator"],
        generator_version=d["generator_version"],
        case_id=d["case_id"],
        rep=d["rep"],
        title=d["title"],
        sections=sections,
        calls=calls,
        wall_ms=d["wall_ms"],
        markdown=d.get("markdown", ""),
        link_hosts=d.get("link_hosts"),
        html=d.get("html"),
        warnings=list(d.get("warnings", [])),
        flagged_numbers=[tuple(x) for x in d.get("flagged_numbers", [])],  # type: ignore[misc]
        error=d.get("error"),
    )


# ---------------------------------------------------------------- run store


class ManifestMismatch(ValueError):
    """Resuming a run with settings that would mix incomparable results."""


class RunStore:
    """Everything stored for one run."""

    def __init__(self, run_id: str, root: Path = RUNS_DIR) -> None:
        if not run_id or "/" in run_id or run_id.startswith("."):
            raise ValueError(f"bad run id: {run_id!r}")
        self.run_id = run_id
        self.root = Path(root)
        self.path = self.root / run_id

    # -- manifest

    @property
    def manifest_path(self) -> Path:
        return self.path / "manifest.json"

    def exists(self) -> bool:
        return self.manifest_path.exists()

    def read_manifest(self) -> dict[str, Any]:
        return json.loads(self.manifest_path.read_text())

    def write_manifest(self, manifest: dict[str, Any]) -> None:
        write_atomic(self.manifest_path, json.dumps(manifest, indent=2, sort_keys=False) + "\n")

    def open_manifest(self, manifest: dict[str, Any]) -> dict[str, Any]:
        """Create the manifest, or merge into an existing one when resuming.

        Resuming may add generators, cases or reps. It refuses a different mode, models,
        effort or a changed version of a generator already in the run.
        """
        if not self.exists():
            manifest = {"run_id": self.run_id, "created_at": now_iso(), **manifest, "updated_at": now_iso()}
            self.write_manifest(manifest)
            return manifest
        old = self.read_manifest()
        for key in ("mode", "models", "effort", "max_tokens"):
            if key in manifest and key in old and old[key] != manifest[key]:
                raise ManifestMismatch(f"run {self.run_id}: {key} was {old[key]!r}, now {manifest[key]!r}")
        old_gens = {g["name"]: g["version"] for g in old.get("generators", [])}
        for g in manifest.get("generators", []):
            if g["name"] in old_gens and old_gens[g["name"]] != g["version"]:
                raise ManifestMismatch(
                    f"run {self.run_id}: {g['name']} was version {old_gens[g['name']]}, now {g['version']}; "
                    "start a new run"
                )
            old_gens[g["name"]] = g["version"]
        merged = dict(old)
        merged["generators"] = [{"name": n, "version": v} for n, v in old_gens.items()]
        cases = dict(zip(old.get("case_ids", []), old.get("case_paths", [])))
        cases.update(zip(manifest.get("case_ids", []), manifest.get("case_paths", [])))
        merged["case_ids"] = list(cases)
        merged["case_paths"] = list(cases.values())
        merged["reps"] = max(old.get("reps", 0), manifest.get("reps", 0))
        for key in ("case_set", "case_set_hash", "judge_version", "git_sha"):
            if key in manifest:
                merged[key] = manifest[key]
        merged["updated_at"] = now_iso()
        self.write_manifest(merged)
        return merged

    def touch_manifest(self, **fields: Any) -> None:
        m = self.read_manifest()
        m.update(fields)
        m["updated_at"] = now_iso()
        self.write_manifest(m)

    # -- documents

    def doc_base(self, generator: str, case_id: str, rep: int) -> Path:
        return self.path / "docs" / generator / case_id / str(rep)

    def has_result(self, generator: str, case_id: str, rep: int, *, ok_only: bool = True) -> bool:
        """A slot is complete when its result exists and (by default) has no error."""
        p = self.doc_base(generator, case_id, rep).with_suffix(".json")
        if not p.exists():
            return False
        if not ok_only:
            return True
        try:
            return json.loads(p.read_text()).get("error") is None
        except json.JSONDecodeError:
            return False

    def save_result(self, result: GenerationResult) -> None:
        base = self.doc_base(result.generator, result.case_id, result.rep)
        write_atomic(base.with_suffix(".json"), json.dumps(result_to_dict(result), indent=2, ensure_ascii=False) + "\n")
        write_atomic(base.with_suffix(".md"), result.markdown)

    def load_result(self, generator: str, case_id: str, rep: int) -> GenerationResult:
        p = self.doc_base(generator, case_id, rep).with_suffix(".json")
        return result_from_dict(json.loads(p.read_text()))

    def iter_results(self) -> Iterator[GenerationResult]:
        docs = self.path / "docs"
        if not docs.exists():
            return
        for p in sorted(docs.glob("*/*/*.json")):
            yield result_from_dict(json.loads(p.read_text()))

    # -- JSONL

    def jsonl_path(self, name: str) -> Path:
        return self.path / f"{name}.jsonl"

    def append(self, name: str, rows: Iterable[dict[str, Any]]) -> None:
        rows = list(rows)
        if not rows:
            return
        p = self.jsonl_path(name)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def rewrite(self, name: str, rows: Iterable[dict[str, Any]]) -> None:
        text = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
        write_atomic(self.jsonl_path(name), text)

    def read(self, name: str) -> list[dict[str, Any]]:
        p = self.jsonl_path(name)
        if not p.exists():
            return []
        rows = []
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue  # a torn last line from an interrupted run
        return rows

    # -- misc

    def write_text(self, rel: str, text: str) -> Path:
        p = self.path / rel
        write_atomic(p, text)
        return p


def latest_run_id(root: Path = RUNS_DIR) -> str | None:
    runs = [p for p in Path(root).glob("*/manifest.json")]
    if not runs:
        return None
    return max(runs, key=lambda p: p.stat().st_mtime).parent.name
