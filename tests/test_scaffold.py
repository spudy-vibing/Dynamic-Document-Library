"""Checks on the packaging and CI scaffold itself (WP-001)."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

ROOT = Path(__file__).resolve().parents[1]
CORE_ALLOWED = {"anthropic", "jinja2", "markdown-it-py", "nh3", "typer", "rich", "tomli"}


def _pyproject() -> dict[str, Any]:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def _name(requirement: str) -> str:
    match = re.match(r"[A-Za-z0-9._-]+", requirement)
    assert match, requirement
    return match.group(0).lower()


def test_nfr_1_python_floor_and_ci_matrix() -> None:
    assert _pyproject()["project"]["requires-python"] == ">=3.10"
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    for version in ("3.10", "3.11", "3.12", "3.13", "3.14"):
        assert f'"{version}"' in ci, version
    for runner in ("ubuntu-latest", "macos-latest", "windows-latest"):
        assert runner in ci, runner


def test_nfr_2_core_dependencies_are_limited() -> None:
    project = _pyproject()["project"]
    deps = project["dependencies"]
    assert {_name(d) for d in deps} <= CORE_ALLOWED
    tomli = [d for d in deps if _name(d) == "tomli"]
    assert tomli
    assert all("python_version < '3.11'" in d for d in tomli)
    extras = project["optional-dependencies"]
    assert {"pdf", "schema", "dev"} <= extras.keys()
    assert [_name(d) for d in extras["pdf"]] == ["weasyprint"]
    assert [_name(d) for d in extras["schema"]] == ["pydantic"]
    assert "pandas" not in {_name(d) for group in extras.values() for d in group}


def test_nfr_4_hatchling_src_layout_and_py_typed() -> None:
    data = _pyproject()
    assert data["build-system"]["build-backend"] == "hatchling.build"
    assert data["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == ["src/doccode"]
    assert (ROOT / "src/doccode/__init__.py").is_file()
    assert (ROOT / "src/doccode/py.typed").is_file()


def test_nfr_5_strict_typing_and_lint_settings() -> None:
    tool = _pyproject()["tool"]
    assert tool["mypy"]["strict"] is True
    assert tool["ruff"]["line-length"] == 100
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    for command in ("ruff check", "ruff format --check", "mypy --strict", "pytest"):
        assert command in makefile, command


def test_nfr_6_coverage_floor_configured() -> None:
    assert _pyproject()["tool"]["coverage"]["report"]["fail_under"] >= 80


def test_nfr_16_gitignore_has_cache_but_not_lock() -> None:
    lines = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".doccode_cache/" in lines
    assert not any("doccode.lock" in line and not line.startswith("#") for line in lines)
