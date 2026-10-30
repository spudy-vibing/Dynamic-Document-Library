"""EVAL-8: nothing under evals/ imports doccode except the adapter."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from evals.checks.check_imports import EVALS_ROOT, check, main, offending_lines


def test_current_tree_passes():
    assert check() == []


@pytest.mark.parametrize(
    "src",
    [
        "import doccode\n",
        "import os, doccode.content as c\n",
        "from doccode import Document\n",
        "from doccode.checks import numbers_from\n",
        "def f():\n    import doccode\n",
        "import importlib\nimportlib.import_module('doccode')\n",
        "__import__('doccode.content')\n",
        "import doccode\nthis is not python(\n",  # fallback scan
    ],
)
def test_detects(src):
    assert offending_lines(src)


@pytest.mark.parametrize("src", ["import doccodex\n", "from .doccode import x\n", "s = 'import doccode'\n", "# import doccode\n"])
def test_ignores(src):
    assert offending_lines(src) == []


def _copy(tmp_path: Path) -> Path:
    root = tmp_path / "evals"
    shutil.copytree(EVALS_ROOT, root, ignore=shutil.ignore_patterns(".venv", "__pycache__", "runs", ".pytest_cache"))
    return root


def test_planted_import_fails(tmp_path, capsys):
    root = _copy(tmp_path)
    (root / "graders" / "sneaky.py").write_text('"""x"""\nimport doccode\n')
    assert check(root) == ["graders/sneaky.py:2"]
    assert main([str(root)]) == 1
    assert "graders/sneaky.py:2" in capsys.readouterr().err


def test_adapter_is_allowed(tmp_path):
    root = _copy(tmp_path)
    adapter = root / "generators" / "doccode_adapter"
    adapter.mkdir(parents=True, exist_ok=True)
    (adapter / "build.py").write_text("from doccode import Document\n")
    assert check(root) == []
    assert main([str(root)]) == 0
