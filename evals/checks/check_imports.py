"""Fail if any module under evals/ imports ``doccode`` outside the DocCode adapter (EVAL-8).

    uv run --project evals python -m evals.checks.check_imports [ROOT]

Flags ``import doccode``, ``import doccode.x``, ``from doccode[.x] import ...`` and
``importlib.import_module("doccode...")`` / ``__import__("doccode...")`` with a literal
name, anywhere in a file (top level or nested). Only files under
``evals/generators/doccode_adapter/`` may do this. Files that do not parse are checked
with a line-based fallback, so a syntax error cannot hide an import.
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

EVALS_ROOT = Path(__file__).resolve().parent.parent
ALLOWED = ("generators", "doccode_adapter")
SKIP_DIRS = {".venv", "__pycache__", ".pytest_cache", "runs", ".git"}
_FALLBACK = re.compile(r"^\s*(?:import\s+doccode\b|from\s+doccode\b)", re.M)


def _is_doccode(name: str | None) -> bool:
    return bool(name) and (name == "doccode" or name.startswith("doccode."))


def offending_lines(source: str) -> list[int]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return [source[: m.start()].count("\n") + 1 for m in _FALLBACK.finditer(source)]
    lines = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(_is_doccode(a.name) for a in node.names):
            lines.append(node.lineno)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and _is_doccode(node.module):
            lines.append(node.lineno)
        elif isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant):
            f = node.func
            fname = f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else ""
            if fname in ("import_module", "__import__") and isinstance(node.args[0].value, str) and _is_doccode(node.args[0].value):
                lines.append(node.lineno)
    return sorted(lines)


def _allowed(rel: Path) -> bool:
    return rel.parts[: len(ALLOWED)] == ALLOWED


def check(root: Path = EVALS_ROOT) -> list[str]:
    """Violations as ``path:line`` strings (paths relative to ``root``); empty when clean."""
    root = Path(root)
    problems = []
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root)
        if any(p in SKIP_DIRS for p in rel.parts) or _allowed(rel):
            continue
        for line in offending_lines(path.read_text(encoding="utf-8", errors="replace")):
            problems.append(f"{rel.as_posix()}:{line}")
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Fail on doccode imports under evals/ outside the adapter.")
    ap.add_argument("root", nargs="?", type=Path, default=EVALS_ROOT)
    args = ap.parse_args(argv)
    problems = check(args.root)
    for p in problems:
        print(f"{p}: imports doccode outside evals/generators/doccode_adapter/", file=sys.stderr)
    if not problems:
        print("ok: no doccode imports outside the adapter")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
