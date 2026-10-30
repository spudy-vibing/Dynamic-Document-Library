"""CLI: ``uv run --project evals python -m evals <command>`` from the repository root.

Commands: project, run, grade, judge, report, compare, all. Every command defaults to
FAKE mode (FakeLLM, no key, no network); ``--live`` uses the owner's ANTHROPIC_API_KEY
and refuses to start when the projected cost exceeds run.max_usd (EVAL-18, EVAL-20).
``--cli`` runs every call through headless Claude Code on the owner's subscription: no
API key and no per-token bill, but the results are UNOFFICIAL and never count toward a gate.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

from evals import runner
from evals.core.config import CONFIG_PATH, load_config
from evals.core.storage import RUNS_DIR, SCOREBOARD_DIR, RunStore, latest_run_id
from evals.generators import DEFAULT_GENERATORS


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--config", type=Path, default=CONFIG_PATH, help=argparse.SUPPRESS)
    p.add_argument("--runs-dir", type=Path, default=RUNS_DIR, help=argparse.SUPPRESS)
    p.add_argument("--scoreboard-dir", type=Path, default=SCOREBOARD_DIR, help=argparse.SUPPRESS)


def _selection(p: argparse.ArgumentParser) -> None:
    p.add_argument("--generators", nargs="+", metavar="NAME", help=f"default: {' '.join(DEFAULT_GENERATORS)}")
    p.add_argument("--cases", nargs="+", metavar="CASE_ID", help="default: every case in run.case_set")
    p.add_argument("--reps", type=int, help="default: run.reps")
    p.add_argument("--case-set", help="default: run.case_set")


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m evals", description="DocCode eval harness (offline by default).")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("project", help="cost projection for a planned run (expected and worst case)")
    _selection(p)
    _common(p)

    for name, help_ in (("run", "generate documents"), ("all", "run, grade, judge, report")):
        p = sub.add_parser(name, help=help_)
        _selection(p)
        backend = p.add_mutually_exclusive_group()
        backend.add_argument("--live", action="store_true", help="call the real API with ANTHROPIC_API_KEY")
        backend.add_argument("--cli", action="store_true", help="UNOFFICIAL: call the model through headless Claude Code (subscription)")
        p.add_argument("--run-id", help="resume this run, or name a new one")
        _common(p)

    for name, help_ in (("grade", "apply every grader"), ("judge", "pairwise judge"), ("report", "write report.md and the scoreboard summary")):
        p = sub.add_parser(name, help=help_)
        p.add_argument("--run-id", help="default: the most recent run")
        if name == "judge":
            backend = p.add_mutually_exclusive_group()
            backend.add_argument("--live", action="store_true", help="judge with the real API")
            backend.add_argument("--cli", action="store_true", help="UNOFFICIAL: judge through headless Claude Code")
        _common(p)

    p = sub.add_parser("compare", help="paired per-metric deltas between two runs")
    p.add_argument("run_a")
    p.add_argument("run_b")
    p.add_argument("--fail-on-regression", action="store_true", help="exit 1 when a regression is found")
    _common(p)
    return ap


def _store(args: argparse.Namespace, run_id: str | None) -> RunStore:
    run_id = run_id or latest_run_id(args.runs_dir)
    if not run_id:
        raise SystemExit("no runs yet; start one with `python -m evals run`")
    store = RunStore(run_id, args.runs_dir)
    if not store.exists():
        raise SystemExit(f"run {run_id} not found in {args.runs_dir}")
    return store


def _plan(args: argparse.Namespace, config: dict[str, Any]) -> tuple[list[Any], list[Any], int, str]:
    case_set = args.case_set or config["run"]["case_set"]
    cases = runner.select_cases(case_set, args.cases)
    gens = runner.select_generators(args.generators, DEFAULT_GENERATORS)
    reps = args.reps or int(config["run"]["reps"])
    return cases, gens, reps, case_set


def cmd_project(args: argparse.Namespace, config: dict[str, Any]) -> int:
    cases, gens, reps, case_set = _plan(args, config)
    print(f"Planned run: {len(cases)} case(s) from {case_set} x {reps} rep(s) x {', '.join(g.name for g in gens)} + judge\n")
    print(runner.project(cases, gens, reps, config).format())
    return 0


def cmd_run(args: argparse.Namespace, config: dict[str, Any]) -> RunStore:
    cases, gens, reps, case_set = _plan(args, config)
    mode = "live" if args.live else "cli" if args.cli else "fake"
    if args.cli:
        runner.require_cli()
        proj = runner.project(cases, gens, reps, config, batches=False)  # CLI mode judges one call at a time
        print(proj.format())
        print("CLI mode: billed to the Claude Code subscription, not per token; the figures above are the API-equivalent price.\n")
    if args.live:
        runner.require_key()
        proj = runner.project(cases, gens, reps, config)
        print(proj.format(), "\n")
        runner.check_budget(proj)
    store = RunStore(args.run_id or runner.new_run_id(mode), args.runs_dir)
    store.open_manifest(runner.build_manifest(cases, gens, reps, config, mode=mode, case_set=case_set))
    print(f"run {store.run_id} ({mode.upper()} mode): {len(gens)} generator(s) x {len(cases)} case(s) x {reps} rep(s)")
    counts = asyncio.run(runner.run_generation(store, cases, gens, reps, config, live=args.live, cli=args.cli))
    print(f"generated {counts['ok']} document(s), {counts['errors']} error(s); {store.path}")
    return store


def cmd_grade(args: argparse.Namespace, config: dict[str, Any], store: RunStore | None = None) -> RunStore:
    store = store or _store(args, args.run_id)
    try:
        from evals.graders import ALL_GRADERS
    except ImportError as e:
        raise SystemExit(f"graders are not available yet ({e})")
    cases = runner.cases_from_manifest(store.read_manifest())
    n = runner.grade_run(store, cases, list(ALL_GRADERS))
    print(f"graded run {store.run_id}: {n} score row(s) from {len(ALL_GRADERS)} grader(s)")
    return store


def cmd_judge(args: argparse.Namespace, config: dict[str, Any], store: RunStore | None = None) -> RunStore:
    store = store or _store(args, args.run_id)
    mode = store.read_manifest().get("mode")
    wanted = "live" if args.live else "cli" if args.cli else "fake"
    if wanted != mode:
        flag = {"live": "--live", "cli": "--cli", "fake": "no backend flag"}[mode]
        raise SystemExit(f"run {store.run_id} is a {mode} run; judge it with {flag} (mixing backends would mislabel the results)")
    if args.cli:
        runner.require_cli()
    manifest = store.read_manifest()
    cases = runner.cases_from_manifest(manifest)
    if args.live:
        runner.require_key()
        if args.cmd == "judge":  # `all --live` already checked the whole run's projection
            gens = runner.select_generators([g["name"] for g in manifest["generators"]], ())
            proj = runner.judge_only(runner.project(cases, gens, int(manifest["reps"]), config))
            print(proj.format(), "\n")
            runner.check_budget(proj)
    counts = asyncio.run(runner.judge_run(store, cases, config, live=args.live, cli=args.cli))
    print(f"judged run {store.run_id}: {counts['orders']} order(s) judged this time, {counts['skipped']} pair slot(s) skipped")
    return store


def cmd_report(args: argparse.Namespace, config: dict[str, Any], store: RunStore | None = None) -> RunStore:
    from evals.report import write_report

    store = store or _store(args, args.run_id)
    report, summary = write_report(store, config, scoreboard_dir=args.scoreboard_dir)
    print(f"report: {report}\nsummary: {summary}")
    return store


def cmd_compare(args: argparse.Namespace, config: dict[str, Any]) -> int:
    from evals.compare import compare_runs, render_compare

    a, b = _store(args, args.run_a), _store(args, args.run_b)
    result = compare_runs(a, b, config)
    text = render_compare(result)
    print(text)
    b.write_text(f"compare_{a.run_id}.md", text)
    return 1 if args.fail_on_regression and result["regressions"] else 0


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    config = load_config(args.config)
    try:
        if args.cmd == "project":
            return cmd_project(args, config)
        if args.cmd == "run":
            cmd_run(args, config)
        elif args.cmd == "grade":
            cmd_grade(args, config)
        elif args.cmd == "judge":
            cmd_judge(args, config)
        elif args.cmd == "report":
            cmd_report(args, config)
        elif args.cmd == "compare":
            return cmd_compare(args, config)
        elif args.cmd == "all":
            store = cmd_run(args, config)
            cmd_grade(args, config, store)
            cmd_judge(args, config, store)
            cmd_report(args, config, store)
    except (runner.BudgetExceeded, runner.MissingKey, runner.MissingCli) as e:
        print(f"refusing to start: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
