"""Tests for scripts/check_ownership.py (DEVELOPMENT_PLAN §0.7)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import check_ownership as co

GIT_ID = [
    "-c",
    "user.name=Test",
    "-c",
    "user.email=test@example.invalid",
    "-c",
    "commit.gpgsign=false",
]


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *GIT_ID, *args], cwd=repo, check=True, capture_output=True)


def _write(repo: Path, rel: str, text: str = "x\n") -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


MANIFEST = 'id = "WP-103"\nwave = 1\nowns = ["src/doccode/execution/graph.py", "tests/graph/**"]\n'


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    _write(tmp_path, "README.md")
    _write(tmp_path, "src/doccode/errors.py")
    _write(tmp_path, "docs/decisions/0001-prior-art.md")
    _write(tmp_path, ".orchestrator/wp/WP-103.toml", MANIFEST)
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "base")
    _git(tmp_path, "checkout", "-q", "-b", "wp/103-graph")
    return tmp_path


@pytest.fixture(autouse=True)
def _no_ci_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("GITHUB_HEAD_REF", "GITHUB_REF_NAME", "GITHUB_BASE_REF"):
        monkeypatch.delenv(var, raising=False)


def test_branch_names_map_to_wp_ids() -> None:
    assert co.wp_id_from_branch("wp/103-graph") == "WP-103"
    assert co.wp_id_from_branch("wp/2F1-fix") == "WP-2F1"
    assert co.wp_id_from_branch("wp/001") == "WP-001"
    assert co.wp_id_from_branch("spike/api") is None
    assert co.wp_id_from_branch("wpx/103-graph") is None


@pytest.mark.parametrize(
    ("path", "owns", "expected"),
    [
        ("src/doccode/execution/graph.py", ["src/doccode/execution/graph.py"], True),
        ("src/doccode/execution/cache.py", ["src/doccode/execution/graph.py"], False),
        ("tests/graph/deep/test_a.py", ["tests/graph/**"], True),
        ("tests/test_graph.py", ["tests/test_*.py"], True),
        ("tests/sub/test_graph.py", ["tests/test_*.py"], False),
        ("evals/x/y/z.py", ["evals/**/*.py"], True),
        ("evals/z.py", ["evals/**/*.py"], True),
        ("examples/01_a.py", ["examples/01_*"], True),
        ("examples/02_a.py", ["examples/0[1]_*"], False),
    ],
)
def test_globs(path: str, owns: list[str], expected: bool) -> None:
    assert co.is_owned(path, owns) is expected


def test_owned_change_passes(repo: Path) -> None:
    _write(repo, "src/doccode/execution/graph.py", "def f(): ...\n")
    _write(repo, "tests/graph/test_cycles.py")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "work")
    assert co.run(repo, None, None) == 0


def test_unowned_change_fails(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write(repo, "src/doccode/errors.py", "changed\n")
    _git(repo, "commit", "-q", "-am", "oops")
    assert co.run(repo, None, None) == 1
    assert "src/doccode/errors.py" in capsys.readouterr().out


def test_uncommitted_and_untracked_files_count(repo: Path) -> None:
    _write(repo, "stray.txt")
    assert co.run(repo, None, None) == 1


def test_deleting_an_unowned_file_fails(repo: Path) -> None:
    _git(repo, "rm", "-q", "README.md")
    _git(repo, "commit", "-q", "-m", "rm")
    assert co.run(repo, None, None) == 1


def test_new_decision_record_always_allowed(repo: Path) -> None:
    _write(repo, "docs/decisions/0009-cr-graph.md")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "cr")
    assert co.run(repo, None, None) == 0


def test_editing_existing_decision_record_fails(repo: Path) -> None:
    _write(repo, "docs/decisions/0001-prior-art.md", "rewritten\n")
    _git(repo, "commit", "-q", "-am", "edit")
    assert co.run(repo, None, None) == 1


def test_branch_cannot_widen_its_own_manifest(repo: Path) -> None:
    _write(repo, ".orchestrator/wp/WP-103.toml", MANIFEST.replace('"tests/graph/**"', '"**"'))
    _write(repo, "src/doccode/errors.py", "changed\n")
    _git(repo, "commit", "-q", "-am", "widen")
    # The manifest is read from the merge base, so both edits are violations.
    assert co.run(repo, None, None) == 1


def test_bootstrap_manifest_from_working_tree(repo: Path) -> None:
    _git(repo, "checkout", "-q", "-b", "wp/001-scaffold", "main")
    _write(
        repo,
        ".orchestrator/wp/WP-001.toml",
        'id = "WP-001"\nowns = ["Makefile", ".orchestrator/wp/WP-001.toml"]\n',
    )
    _write(repo, "Makefile")
    assert co.run(repo, None, None) == 0


def test_missing_manifest_is_a_config_error(repo: Path) -> None:
    _git(repo, "checkout", "-q", "-b", "wp/999-nothing", "main")
    assert co.main(["--repo", str(repo)]) == 2


def test_manifest_id_must_match_branch() -> None:
    with pytest.raises(co.OwnershipConfigError, match="expected 'WP-104'"):
        co.parse_manifest(MANIFEST, expected_id="WP-104", source="m")


def test_non_wp_branch_is_skipped(repo: Path) -> None:
    _git(repo, "checkout", "-q", "main")
    _write(repo, "anything.txt")
    assert co.run(repo, None, None) == 0


def test_ci_variables_select_branch_and_base(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _git(repo, "checkout", "-q", "main")
    monkeypatch.setenv("GITHUB_HEAD_REF", "wp/103-graph")
    monkeypatch.setenv("GITHUB_BASE_REF", "nonexistent")
    # origin/nonexistent does not exist, so resolution falls back to main.
    assert co.resolve_base(repo, None) == "main"
    assert co.current_branch(repo) == "wp/103-graph"
