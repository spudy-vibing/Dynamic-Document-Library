"""The eval CI workflows (WP-019): the offline job holds no secret, the live job is manual and gated.

Parses .github/workflows/evals-*.yml. GitHub cannot be run here, so these tests pin the
properties that matter: triggers, permissions, where the key appears, the order of steps, and
the cap check, which is executed for real against the committed config.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from evals.core.cases import load_case_set
from evals.generators import GENERATORS

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
OFFLINE = WORKFLOWS / "evals-offline.yml"
LIVE = WORKFLOWS / "evals-live.yml"
REPO = "spudy-vibing/Dynamic-Document-Library"


def _load(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    # PyYAML follows YAML 1.1, where the bare key `on` parses as True.
    if True in data:
        data["on"] = data.pop(True)
    return data


def _steps(wf: dict[str, Any]) -> list[dict[str, Any]]:
    return [s for job in wf["jobs"].values() for s in job["steps"]]


def _runs(wf: dict[str, Any]) -> str:
    return "\n".join(s.get("run", "") for s in _steps(wf))


@pytest.fixture(scope="module")
def offline() -> dict[str, Any]:
    return _load(OFFLINE)


@pytest.fixture(scope="module")
def live() -> dict[str, Any]:
    return _load(LIVE)


# ---------------------------------------------------------------- offline job


def test_eval_14_offline_job_runs_on_every_pr_and_default_branch_push(offline: dict[str, Any]) -> None:
    on = offline["on"]
    assert "pull_request" in on and on["pull_request"] in (None, {})  # every PR, no branch filter
    assert set(on["push"]["branches"]) == {"master", "main"}
    assert offline["permissions"] == {"contents": "read"}


def test_eval_18_offline_job_uses_no_secret_and_no_live_backend(offline: dict[str, Any]) -> None:
    text = OFFLINE.read_text(encoding="utf-8")
    assert "secrets." not in text
    assert "ANTHROPIC_API_KEY" not in text
    assert "environment" not in offline["jobs"]["offline"]
    runs = _runs(offline)
    assert "--live" not in runs and "--cli" not in runs


def test_eval_8_eval_14_offline_job_runs_suite_import_check_cases_and_fake_smoke(offline: dict[str, Any]) -> None:
    runs = _runs(offline)
    assert "uv run --project evals pytest" in runs
    assert "python -m evals.checks.check_imports" in runs
    assert "python -m evals.fixtures.make_cases --check" in runs
    assert "python -m evals all" in runs and "--reps 1" in runs
    assert "python -m evals compare" in runs and "--fail-on-regression" in runs


def test_offline_job_caches_uv_on_the_eval_lock(offline: dict[str, Any]) -> None:
    setup = next(s for s in _steps(offline) if s.get("uses", "").startswith("astral-sh/setup-uv"))
    assert setup["with"]["enable-cache"] is True
    assert setup["with"]["cache-dependency-glob"] == "evals/uv.lock"


def _smoke_names() -> list[tuple[str, list[str], list[str]]]:
    """(source, cases, generators) for every smoke selection the CI files hard-code."""
    env = _load(OFFLINE)["env"]
    inputs = _load(LIVE)["on"]["workflow_dispatch"]["inputs"]
    make = (ROOT / "Makefile").read_text(encoding="utf-8")

    def make_var(name: str) -> list[str]:
        m = re.search(rf"^{name} \?= (.+)$", make, re.M)
        assert m, name
        return m.group(1).split()

    return [
        ("evals-offline.yml", env["SMOKE_CASES"].split(), env["SMOKE_GENERATORS"].split()),
        ("evals-live.yml", inputs["cases"]["default"].split(), inputs["generators"]["default"].split()),
        ("Makefile", make_var("EVALS_SMOKE_CASES"), make_var("EVALS_SMOKE_GENERATORS")),
    ]


@pytest.mark.parametrize(("source", "cases", "gens"), _smoke_names(), ids=lambda v: v if isinstance(v, str) else "")
def test_ci_smoke_selections_name_real_cases_and_generators(source: str, cases: list[str], gens: list[str]) -> None:
    dev = {c.id for c in load_case_set("dev")}
    assert cases and set(cases) <= dev, f"{source}: unknown case(s) {set(cases) - dev}"
    assert gens and set(gens) <= set(GENERATORS), f"{source}: unknown generator(s) {set(gens) - set(GENERATORS)}"


# ---------------------------------------------------------------- live job


def test_eval_18_live_job_is_manual_only(live: dict[str, Any]) -> None:
    assert set(live["on"]) == {"workflow_dispatch"}  # never push, pull_request or pull_request_target
    inputs = live["on"]["workflow_dispatch"]["inputs"]
    assert set(inputs) == {"case_set", "cases", "generators", "reps", "max_usd"}
    assert inputs["case_set"]["options"] == ["dev"]  # held-out runs stay local (EVAL-6)


def test_eval_18_live_job_runs_behind_the_owner_approved_environment(live: dict[str, Any]) -> None:
    (job,) = live["jobs"].values()
    assert job["environment"] == "eval-live"
    assert "github.event_name == 'workflow_dispatch'" in job["if"]
    assert f"github.repository == '{REPO}'" in job["if"]  # skipped in forks
    assert live["permissions"] == {"contents": "read"}  # cannot commit or push run data


def test_eval_18_key_reaches_only_the_live_step(live: dict[str, Any]) -> None:
    text = LIVE.read_text(encoding="utf-8")
    assert text.count("secrets.") == 1
    holders = [s for s in _steps(live) if "secrets." in yaml.safe_dump(s)]
    assert len(holders) == 1
    (step,) = holders
    assert step["env"]["ANTHROPIC_API_KEY"] == "${{ secrets.ANTHROPIC_API_KEY }}"
    assert "python -m evals all --live" in step["run"]
    assert "ANTHROPIC_API_KEY" not in yaml.safe_dump(live.get("env", {}))
    (job,) = live["jobs"].values()
    assert "ANTHROPIC_API_KEY" not in yaml.safe_dump(job.get("env", {}))


def test_eval_20_live_job_projects_and_caps_before_running(live: dict[str, Any]) -> None:
    names = [s.get("name", s.get("uses", "")) for s in _steps(live)]
    project = next(i for i, s in enumerate(_steps(live)) if "python -m evals project" in s.get("run", ""))
    cap = next(i for i, n in enumerate(names) if n.startswith("Refuse above the cap"))
    run = next(i for i, s in enumerate(_steps(live)) if "--live" in s.get("run", ""))
    assert project < cap < run


def test_eval_18_live_job_never_commits_and_uploads_artifacts(live: dict[str, Any]) -> None:
    runs = _runs(live)
    assert not re.search(r"\bgit\s+(commit|push|add)\b", runs)
    checkout = next(s for s in _steps(live) if s.get("uses", "").startswith("actions/checkout"))
    assert checkout["with"]["persist-credentials"] is False
    uploads = [s for s in _steps(live) if s.get("uses", "").startswith("actions/upload-artifact")]
    paths = "\n".join(s["with"]["path"] for s in uploads)
    assert "report.md" in paths and ".summary.json" in paths
    assert all(s.get("if") == "always()" for s in uploads)


def test_live_job_inputs_reach_the_shell_only_through_env(live: dict[str, Any]) -> None:
    for step in _steps(live):
        assert "inputs." not in step.get("run", ""), step.get("name")


# ---------------------------------------------------------------- the cap check, executed


def _cap_script(live: dict[str, Any]) -> str:
    step = next(s for s in _steps(live) if s.get("name", "").startswith("Refuse above the cap"))
    m = re.search(r"<<'PY'\n(.*?)\nPY\s*$", step["run"], re.S)
    assert m, "cap step must hold a python heredoc"
    return m.group(1)


def _run_cap(live: dict[str, Any], max_usd: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    env.update(MAX_USD=max_usd, PYTHONPATH=str(ROOT))
    args = ["--case-set", "dev", "--reps", "1", "--generators", "b0-oneshot", "b1-parallel",
            "--cases", "w-calderwyn-5120"]
    return subprocess.run([sys.executable, "-", *args], input=_cap_script(live), text=True,
                          capture_output=True, cwd=ROOT, env=env, timeout=120)


def test_eval_20_live_cap_check_refuses_above_the_input_cap(live: dict[str, Any]) -> None:
    proc = _run_cap(live, "0.0001")
    assert proc.returncode == 2, proc.stderr
    assert "refusing to start" in proc.stdout


def test_eval_20_live_cap_check_passes_under_the_cap_and_never_exceeds_config(live: dict[str, Any]) -> None:
    proc = _run_cap(live, "100000")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    # The input can only lower the cap: the effective cap is run.max_usd from config.toml.
    assert re.search(r"cap \$(\d+\.\d\d) \(input max_usd \$100000\.00, run\.max_usd \$\1\)", proc.stdout), proc.stdout
