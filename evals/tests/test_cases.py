"""Dev cases: they load, they regenerate byte-for-byte from their seeds (EVAL-7), and the traps are real."""

from __future__ import annotations

from pathlib import Path

import pytest

from evals.core.cases import load_case, load_case_set
from evals.core.render import build_data_block
from evals.fixtures import make_cases, synth_company, synth_repo
from evals.graders.numbers import close, supported_values

DEV = make_cases.DEV_DIR


@pytest.fixture(scope="module")
def cases():
    return load_case_set("dev")


def test_six_mvp_cases_load_and_validate(cases):
    assert len(cases) == 6
    families = sorted(c.family for c in cases)
    assert families == ["f1_weekly", "f1_weekly", "f2_quarterly", "f2_quarterly", "f2_quarterly", "f3_readme"]
    assert len({c.id for c in cases}) == 6
    for c in cases:
        assert c.path.name == c.id and c.path.parent.name == c.family
        for s in c.sections:
            for spec in s.data_blocks:
                build_data_block(c, spec)  # every data block renders


def test_committed_cases_match_regeneration(tmp_path: Path):
    """CI regenerates every case from its seed and fails on any byte difference (EVAL-7)."""
    make_cases.write_all(tmp_path)
    fresh = {p.relative_to(tmp_path).as_posix(): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    committed = {
        p.relative_to(DEV).as_posix(): p.read_bytes()
        for fam in make_cases.FAMILIES
        for p in (DEV / fam).rglob("*")
        if p.is_file()
    }
    assert sorted(fresh) == sorted(committed), "case files added or removed; run python -m evals.fixtures.make_cases"
    changed = [k for k in fresh if fresh[k] != committed[k]]
    assert not changed, f"regenerated files differ from committed ones: {changed}"
    for case_toml in tmp_path.glob("*/*/case.toml"):
        load_case(case_toml.parent)


def test_generators_are_pure_functions_of_seed():
    assert synth_company.quarterly(1903, bad_news=True) == synth_company.quarterly(1903, bad_news=True)
    assert synth_company.weekly(5120) == synth_company.weekly(5120)
    assert synth_repo.repo(7781) == synth_repo.repo(7781)
    assert synth_company.quarterly(1903).data != synth_company.quarterly(1904).data


def _f2(cases):
    return [c for c in cases if c.family == "f2_quarterly"]


def test_quarterly_truth_matches_data(cases):
    for c in _f2(cases):
        assert c.truth["values"] == c.data["metrics"]
        d = c.truth["derived"]
        m = c.data["metrics"]
        assert d["revenue_change"]["value"] == m["revenue_this"] - m["revenue_prior"]
        assert d["revenue_growth_pct"]["value"] == round((m["revenue_this"] / m["revenue_prior"] - 1) * 100, 1)


def test_trap_t1_derivable_not_given_and_allowed(cases):
    allowed_cases = [c for c in _f2(cases) if c.traps.allowed_derived]
    assert allowed_cases, "at least one quarterly case must allow growth via allowed_derived"
    for c in _f2(cases):
        sup = [v for _, v in supported_values(c)]
        allowed = {a.key for a in c.traps.allowed_derived}
        assert c.traps.derivable_not_given
        for key in c.traps.derivable_not_given:
            assert key not in allowed
            v = c.truth["derived"][key]["value"]
            assert not any(close(v, s, 0.005) for s in sup), f"{c.id}: derivable {key} collides with a given figure"
        for key in allowed:
            v = c.truth["derived"][key]["value"]
            assert any(close(v, s, 0.005) for s in sup)


def test_trap_t2_near_collision(cases):
    for c in _f2(cases):
        (a, b), = c.traps.near_collision
        va, vb = c.truth["values"][a], c.truth["values"][b]
        assert va != vb and abs(va - vb) <= 0.31
        assert not close(va, vb, 0.005), "the pair must stay distinguishable by the number grader"


def test_trap_t3_unit_raw_values(cases):
    unit_cases = [c for c in _f2(cases) if c.traps.unit]
    assert unit_cases
    for c in unit_cases:
        for key in c.traps.unit:
            v = c.truth["values"][key]
            assert isinstance(v, int) and v >= 1_000_000 and v % 100_000 == 0


def test_trap_t4_missing_is_null_everywhere(cases):
    assert sum(1 for c in cases if c.traps.missing) >= 4
    for c in cases:
        for key in c.traps.missing:
            assert c.truth["values"][key] is None
            assert c.truth["labels"][key]


def test_trap_t5_bad_news_revenue_fell(cases):
    bad = [c for c in _f2(cases) if c.traps.bad_news]
    assert bad
    for c in _f2(cases):
        m = c.data["metrics"]
        assert (m["revenue_this"] < m["revenue_prior"]) == c.traps.bad_news


def test_figures_are_distinctive(cases):
    for c in cases:
        nums = {k: v for k, v in c.truth["values"].items() if v is not None}
        exempt = tuple(tuple(p) for p in c.traps.near_collision)
        assert synth_company.distinct(nums, exempt=exempt), c.id
        for k, v in nums.items():
            if k.endswith("_pct"):
                assert 0 < v < 100, f"{c.id}: {k} must be a percent number"


def test_weekly_fans_in_and_attachments_match_data(cases):
    for c in [c for c in cases if c.family == "f1_weekly"]:
        team_ids = [s.id for s in c.sections if s.id not in ("overview", "next_week")]
        assert len(team_ids) == 3
        assert c.section("next_week").depends_on == tuple(team_ids)
        assert sorted(c.attachments) == sorted(f"{t}_notes.md" for t in team_ids)
        for team in c.data["teams"]:
            slug = next(t for t in team_ids if c.section(t).title == team["team"])
            note = c.attachments[f"{slug}_notes.md"]
            assert f"{team['metrics']['tickets_closed']} tickets closed" in note


def test_readme_facts_match_attachments(cases):
    (c,) = [c for c in cases if c.family == "f3_readme"]
    facts = c.truth["facts"]
    py = c.attachments["pyproject.toml"]
    assert f'name = "{c.data["package"]}"' in py
    assert facts["install_command"] == f"pip install {c.data['package']}"
    assert f'requires-python = "{facts["python_floor"]}"' in py
    assert f'license = "{facts["license"]}"' in py
    help_text = c.attachments["cli_help.txt"]
    for flag in facts["cli_flags"].replace("-h/", "").split(", "):
        assert flag in help_text
    for key in ("workers", "timeout_s", "retries"):
        assert f"(default: {c.truth['values'][key]})" in help_text
