#!/usr/bin/env python3
"""Fail if a work-package branch touches files outside its manifest (DEVELOPMENT_PLAN §0.7).

The WP ID comes from the branch name ``wp/<id>-<slug>`` (``wp/103-graph`` -> ``WP-103``,
``wp/1T-tests`` -> ``WP-1T``, ``wp/208a-scheduler`` -> ``WP-208a``). The manifest
``.orchestrator/wp/<WP-ID>.toml`` lists the paths the WP may write in ``owns``; globs are
allowed (``*`` stays inside one path segment, ``**`` spans segments, ``?`` is one character).

Every path the branch changes relative to its merge base with the base branch must match
``owns``. A deleted or renamed file counts as a change to the old path too. New files under
``docs/decisions/`` are always allowed (§0.4); edits to existing decision records are not.
Uncommitted and untracked files are included, so ``make check`` catches a stray edit before
it is committed.

The manifest is read from the merge base when it exists there, so a branch cannot widen its
own ``owns``. Only a WP whose manifest is not yet on the base branch (the WP-001 bootstrap)
falls back to the working tree.

Branches that are not ``wp/...`` (``main``, ``spike/api``, orchestrator branches) are skipped.

Exit codes: 0 clean or skipped, 1 ownership violation, 2 configuration error.

Example::

    python scripts/check_ownership.py                       # current branch vs main
    python scripts/check_ownership.py --branch wp/103-graph --base origin/main
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import re
import subprocess
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised on Python 3.10 only
    import tomli as tomllib

MANIFEST_DIR = ".orchestrator/wp"
ALWAYS_ALLOWED_NEW = ("docs/decisions/",)
BRANCH_RE = re.compile(r"^wp/(?P<id>[0-9][0-9A-Za-z]*)(?:-.*)?$")


class OwnershipConfigError(Exception):
    """The check cannot run: missing manifest, bad manifest, unknown base branch."""


@dataclass(frozen=True)
class Change:
    """One changed path. ``status`` is a git name-status letter (A, M, D, T, ...)."""

    status: str
    path: str


@dataclass(frozen=True)
class Manifest:
    """The fields of ``.orchestrator/wp/<WP-ID>.toml`` this check uses."""

    id: str
    owns: tuple[str, ...]
    source: str


def wp_id_from_branch(branch: str) -> str | None:
    """Return ``WP-<id>`` for a ``wp/<id>-<slug>`` branch, else ``None``.

    >>> wp_id_from_branch("wp/001-scaffold")
    'WP-001'
    >>> wp_id_from_branch("wp/208a-scheduler")
    'WP-208a'
    >>> wp_id_from_branch("refs/heads/wp/1T-tests")
    'WP-1T'
    >>> wp_id_from_branch("main") is None
    True
    """
    branch = branch.removeprefix("refs/heads/")
    match = BRANCH_RE.match(branch)
    return f"WP-{match.group('id')}" if match else None


def glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Compile an ``owns`` glob to a regex over POSIX paths.

    >>> bool(glob_to_regex("src/doccode/**").match("src/doccode/core/prompt.py"))
    True
    >>> bool(glob_to_regex("src/*.py").match("src/a/b.py"))
    False
    >>> bool(glob_to_regex("evals/**/test_*.py").match("evals/test_x.py"))
    True
    """
    out: list[str] = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        elif pattern[i] == "[":
            end = pattern.find("]", i + 1)
            if end == -1:
                out.append(re.escape("["))
                i += 1
            else:
                out.append(fnmatch.translate(pattern[i : end + 1])[4:-3])
                i = end + 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


def is_owned(path: str, owns: Iterable[str]) -> bool:
    """Return whether ``path`` matches any ``owns`` glob. A trailing ``/`` means the subtree.

    >>> is_owned("scripts/x.py", ["scripts/**"])
    True
    >>> is_owned("scripts/x.py", ["scripts/"])
    True
    >>> is_owned("src/doccode/errors.py", ["src/doccode/core/**"])
    False
    """
    for pattern in owns:
        if pattern.endswith("/"):
            pattern += "**"
        if glob_to_regex(pattern).match(path):
            return True
    return False


def violations(changes: Iterable[Change], owns: Sequence[str]) -> list[Change]:
    """Return the changes the manifest does not allow.

    >>> violations([Change("A", "docs/decisions/0009-x.md")], [])
    []
    >>> violations([Change("M", "docs/decisions/0001-prior-art.md")], [])
    [Change(status='M', path='docs/decisions/0001-prior-art.md')]
    """
    bad: list[Change] = []
    for change in changes:
        if change.status == "A" and change.path.startswith(ALWAYS_ALLOWED_NEW):
            continue
        if not is_owned(change.path, owns):
            bad.append(change)
    return bad


def parse_manifest(text: str, *, expected_id: str, source: str) -> Manifest:
    """Parse and validate a manifest's TOML text."""
    try:
        data: dict[str, Any] = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise OwnershipConfigError(f"{source}: invalid TOML: {exc}") from exc
    if data.get("id") != expected_id:
        raise OwnershipConfigError(
            f"{source}: id is {data.get('id')!r}, expected {expected_id!r} from the branch name"
        )
    owns = data.get("owns")
    if not isinstance(owns, list) or not all(isinstance(p, str) and p for p in owns):
        raise OwnershipConfigError(f"{source}: 'owns' must be a list of non-empty path globs")
    return Manifest(id=expected_id, owns=tuple(owns), source=source)


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=False)
    if check and result.returncode != 0:
        raise OwnershipConfigError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result


def current_branch(repo: Path) -> str:
    """The branch to check: CI variables first, then the checked-out branch."""
    for var in ("GITHUB_HEAD_REF", "GITHUB_REF_NAME"):
        value = os.environ.get(var, "").strip()
        if value:
            return value
    return _git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()


def resolve_base(repo: Path, base: str | None) -> str:
    """Pick the base ref: ``--base``, then the PR base in CI, then main/master locally."""
    candidates: list[str] = []
    if base:
        candidates.append(base)
    else:
        pr_base = os.environ.get("GITHUB_BASE_REF", "").strip()
        if pr_base:
            candidates.append(f"origin/{pr_base}")
        candidates += ["main", "origin/main", "master", "origin/master"]
    for ref in candidates:
        if (
            _git(
                repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", check=False
            ).returncode
            == 0
        ):
            return ref
    raise OwnershipConfigError(f"no base branch found (tried {', '.join(candidates)})")


def changed_paths(repo: Path, merge_base: str) -> list[Change]:
    """Changes from ``merge_base`` to the working tree, plus untracked files."""
    diff = _git(repo, "diff", "--name-status", "--no-renames", "-z", merge_base).stdout
    fields = [f for f in diff.split("\0") if f]
    changes = [Change(fields[i][0], fields[i + 1]) for i in range(0, len(fields) - 1, 2)]
    untracked = _git(repo, "ls-files", "--others", "--exclude-standard", "-z").stdout
    changes += [Change("A", p) for p in untracked.split("\0") if p]
    return sorted(set(changes), key=lambda c: (c.path, c.status))


def load_manifest(repo: Path, wp_id: str, merge_base: str) -> Manifest:
    """Read the manifest from the merge base, else (bootstrap) from the working tree."""
    rel = f"{MANIFEST_DIR}/{wp_id}.toml"
    shown = _git(repo, "show", f"{merge_base}:{rel}", check=False)
    if shown.returncode == 0:
        return parse_manifest(shown.stdout, expected_id=wp_id, source=f"{rel} (at merge base)")
    path = repo / rel
    if not path.is_file():
        raise OwnershipConfigError(
            f"no manifest for {wp_id}: {rel} is missing. "
            "The orchestrator writes it before dispatch."
        )
    text = path.read_text(encoding="utf-8")
    return parse_manifest(text, expected_id=wp_id, source=f"{rel} (working tree, not on base yet)")


def run(repo: Path, branch: str | None, base: str | None) -> int:
    """Run the check and print a report. Returns the process exit code."""
    branch = branch or current_branch(repo)
    wp_id = wp_id_from_branch(branch)
    if wp_id is None:
        print(f"check_ownership: branch {branch!r} is not a wp/<id>-<slug> branch; skipped.")
        return 0
    base_ref = resolve_base(repo, base)
    merge_base = _git(repo, "merge-base", base_ref, "HEAD").stdout.strip()
    manifest = load_manifest(repo, wp_id, merge_base)
    changes = changed_paths(repo, merge_base)
    bad = violations(changes, manifest.owns)
    print(
        f"check_ownership: {wp_id} on {branch!r}, base {base_ref} ({merge_base[:12]}), "
        f"manifest {manifest.source}; {len(changes)} changed path(s)."
    )
    if not bad:
        print("check_ownership: OK, every changed path is owned.")
        return 0
    print(f"check_ownership: FAIL, {len(bad)} path(s) outside 'owns' of {wp_id}:")
    for change in bad:
        print(f"  {change.status}  {change.path}")
    print("Stop and file a Contract Request (DEVELOPMENT_PLAN §0.4) instead of editing these.")
    return 1


def main(argv: Sequence[str] | None = None) -> int:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    parser.add_argument("--branch", help="branch name (default: CI variables or HEAD)")
    parser.add_argument("--base", help="base ref (default: PR base, else main/master)")
    parser.add_argument("--repo", type=Path, default=Path.cwd(), help="repository root")
    args = parser.parse_args(argv)
    try:
        return run(args.repo, args.branch, args.base)
    except OwnershipConfigError as exc:
        print(f"check_ownership: ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
