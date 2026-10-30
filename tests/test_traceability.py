"""Tests for scripts/traceability.py (DEVELOPMENT_PLAN §6)."""

from __future__ import annotations

from pathlib import Path

import pytest

import traceability as tr

ROOT = Path(__file__).resolve().parents[1]

BRD = """
### 4.3 Lazy references
- **FR-REF-1** MUST: tokens.
- **FR-REF-2** SHOULD: edges.
### 5.1 Platform
- **NFR-1** MUST: Python 3.10. See also FR-REF-1.
- **EVAL-1** MAY: cases.
"""

PLAN = """
### Wave 0 — scaffold

| WP | Mode | Title | Req | Depends |
| --- | --- | --- | --- | --- |
| WP-001 | P | Scaffold, mentions FR-REF-2 in the title only | NFR-1 | — |
| WP-010 | P | Evals | EVAL-1 | WP-000 |

### Wave 1 — authoring

| WP | Mode | Title | Req | Depends |
| --- | --- | --- | --- | --- |
| WP-101 | P | Prompt \\| refs | FR-REF-1..2 | WP-003 |

## 3. Elsewhere

| WP-999 | not a wave table | NFR-1 |
"""


def _report(
    brd: str = BRD,
    plan: str = PLAN,
    tests: dict[str, set[str]] | None = None,
    completed: tuple[int, ...] = (),
    manifests: dict[str, set[tuple[int, str]]] | None = None,
) -> tr.Report:
    return tr.build_report(
        brd_text=brd,
        plan_text=plan,
        manifest_schedule=manifests or {},
        tests=tests or {},
        completed_waves=completed,
    )


def test_ranges_and_lists_expand() -> None:
    assert tr.extract_ids("FR-DOC-1..3") == ["FR-DOC-1", "FR-DOC-2", "FR-DOC-3"]
    assert tr.extract_ids("EVAL-1..3, 18, 20..21") == [
        "EVAL-1",
        "EVAL-2",
        "EVAL-3",
        "EVAL-18",
        "EVAL-20",
        "EVAL-21",
    ]
    assert tr.extract_ids("NFR-4..6, NFR-16") == ["NFR-4", "NFR-5", "NFR-6", "NFR-16"]
    assert tr.extract_ids("Python 3.10, NFR-1 and 3.11") == ["NFR-1"]


def test_plan_req_column_sets_wave_and_wp() -> None:
    schedule = tr.parse_plan(PLAN)
    assert schedule["NFR-1"] == {(0, "WP-001")}
    assert schedule["FR-REF-2"] == {(1, "WP-101")}  # the Title cell does not schedule
    assert "WP-999" not in {wp for pairs in schedule.values() for _, wp in pairs}


def test_clean_documents_pass() -> None:
    report = _report()
    assert report.ok
    assert report.requirements["FR-REF-1"].status == "planned"
    assert report.requirements["FR-REF-1"].waves == {1}


def test_dangling_id_in_plan_fails() -> None:
    report = _report(plan=PLAN + "\nAlso FR-REF-7 and NFR-99.\n")
    assert not report.ok
    assert set(report.dangling) == {"FR-REF-7", "NFR-99"}


def test_dangling_id_in_brd_body_fails() -> None:
    report = _report(brd=BRD + "\nSee EVAL-40.\n")
    assert report.dangling == {"EVAL-40": {"BRD"}}


def test_dangling_id_in_test_name_fails() -> None:
    report = _report(tests={"FR-REF-9": {"tests/test_x.py::test_fr_ref_9_x"}})
    assert "FR-REF-9" in report.dangling


def test_dangling_id_in_manifest_fails() -> None:
    report = _report(manifests={"NFR-50": {(0, "WP-001")}})
    assert report.dangling == {"NFR-50": {"manifests"}}


def test_requirement_missing_from_plan_fails() -> None:
    report = _report(brd=BRD + "- **NFR-2** MUST: deps.\n")
    assert report.unreferenced == ["NFR-2"]
    assert not report.ok


def test_duplicate_definition_fails() -> None:
    report = _report(brd=BRD + "- **NFR-1** MUST: again.\n")
    assert report.duplicates == ["NFR-1"]


def test_completed_wave_must_needs_a_test() -> None:
    assert _report(completed=(0,)).untested_due == ["NFR-1"]
    tested = _report(completed=(0,), tests={"NFR-1": {"tests/t.py::test_nfr_1_floor"}})
    assert tested.ok
    assert tested.requirements["NFR-1"].status == "tested"


def test_completed_wave_ignores_should_and_may() -> None:
    # Wave 1 schedules FR-REF-1 (MUST) and FR-REF-2 (SHOULD); only the MUST is due.
    assert _report(completed=(1,)).untested_due == ["FR-REF-1"]


def test_scan_tests_finds_cited_ids(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text(
        "def test_fr_ref_1_fstring_creates_edge(): ...\n"
        "def test_nfr_6_and_eval_8_both(): ...\n"
        "def test_evaluate_nothing(): ...\n"
        "def helper_fr_ref_2(): ...\n",
        encoding="utf-8",
    )
    found = tr.scan_tests([tmp_path / "tests", tmp_path / "missing"], tmp_path)
    assert found == {
        "FR-REF-1": {"tests/test_a.py::test_fr_ref_1_fstring_creates_edge"},
        "NFR-6": {"tests/test_a.py::test_nfr_6_and_eval_8_both"},
        "EVAL-8": {"tests/test_a.py::test_nfr_6_and_eval_8_both"},
    }


def test_markdown_report_has_one_row_per_requirement() -> None:
    text = tr.render_markdown(_report())
    assert "| Requirement ID | Level | Wave | WP | Tests | Status |" in text
    assert "| NFR-1 | MUST | 0 | WP-001 | — | planned |" in text
    assert text.count("\n| ") == 2 + 4


def test_main_writes_report(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "brd.md").write_text(BRD, encoding="utf-8")
    (tmp_path / "plan.md").write_text(PLAN, encoding="utf-8")
    code = tr.main(
        ["--repo", str(tmp_path), "--brd", "brd.md", "--plan", "plan.md", "--write", "out/t.md"]
    )
    assert code == 0
    assert (tmp_path / "out" / "t.md").is_file()
    assert "traceability: OK." in capsys.readouterr().out


def test_real_brd_and_plan_have_no_dangling_ids() -> None:
    """The repository's own BRD, plan, manifests and tests pass the §6 check."""
    assert tr.main(["--repo", str(ROOT)]) == 0
