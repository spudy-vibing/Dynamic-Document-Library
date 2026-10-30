"""Seeded synthetic Python repositories for README cases (F3).

A pure function of ``seed``: the same seed always gives the same files and truth.
The repo is described by three attachments (a ``pyproject.toml`` excerpt, the CLI's
``--help`` text and the first line of ``LICENSE``) plus a small data.json. The facts a
README must get right (install command, CLI flags, licence, Python floor) are in
truth.json "facts"; numeric defaults (timeouts, worker counts) are in "values".

Python floors are drawn from 3.9, 3.11 and 3.12 only: "3.10" would parse as the number
3.1, which a number grader cannot tell apart from 3.1.
"""

from __future__ import annotations

import random
from typing import Any

from evals.fixtures.synth_company import Fixture, _odd_int, distinct

NAMES = ("brindle", "quorvane", "tesselate", "marrowgate", "halcyx", "ostrelix", "vellum-kit", "calder")
PURPOSES = (
    ("sync", "Mirror a directory tree to object storage, skipping unchanged files."),
    ("lint", "Check Markdown files for broken links and heading problems."),
    ("audit", "Scan a project's lock file and report packages with known licence conflicts."),
    ("pack", "Bundle a static site into a single archive with content hashes."),
)
LICENSES = (
    ("Apache-2.0", "Apache License, Version 2.0"),
    ("MIT", "MIT License"),
    ("BSD-3-Clause", "BSD 3-Clause License"),
    ("MPL-2.0", "Mozilla Public License Version 2.0"),
)
FLOORS = ("3.9", "3.11", "3.12")
AUTHORS = ("Ines Varga", "Kenji Moreau", "Ama Brennan", "Dario Nwosu")


def repo(seed: int, *, extras: bool = False, env: bool = False, changelog: bool = False) -> Fixture:
    """A small CLI package. Options are drawn after every default figure, so a seed's default
    repo is the same with or without them:

    * ``extras``: optional dependency groups, so the install section has a second command;
    * ``env``: environment variables, listed in the ``--help`` text;
    * ``changelog``: a ``CHANGELOG.md`` attachment that records a **removed** flag (truth
      facts ``removed_flags``). Listing it as a current option is a fabricated flag, which
      ``graders/flags.py`` scores; saying that it was removed is fine.
    """
    rng = random.Random(seed)
    base = rng.choice(NAMES)
    verb, description = rng.choice(PURPOSES)
    package = f"{base}-{verb}"
    command = f"{base.split('-')[0]}{verb}"
    spdx, license_name = rng.choice(LICENSES)
    floor = rng.choice(FLOORS)
    version = f"0.{rng.randint(2, 9)}.{rng.randint(1, 9)}"
    author = rng.choice(AUTHORS)

    for _ in range(1000):
        timeout = _odd_int(rng, 15, 95)
        workers = _odd_int(rng, 11, 48)
        retries = rng.randint(11, 19)  # above 10 so the grader does not exclude it
        if distinct({"timeout_s": timeout, "workers": workers, "retries": retries, "python_floor": float(floor)}):
            break

    flags = [
        {"flag": "--config PATH", "help": f"Read settings from PATH (default: ./{command}.toml)", "default": f"./{command}.toml"},
        {"flag": "--workers N", "help": f"Number of parallel workers (default: {workers})", "default": workers},
        {"flag": "--timeout SECONDS", "help": f"Per-request timeout in seconds (default: {timeout})", "default": timeout},
        {"flag": "--retries N", "help": f"Retries per failed request (default: {retries})", "default": retries},
        {"flag": "--dry-run", "help": "Print what would happen without changing anything", "default": False},
        {"flag": "--verbose", "help": "Log every file considered", "default": False},
    ]
    flag_names = [f["flag"].split()[0] for f in flags]

    pyproject = "\n".join(
        [
            "[project]",
            f'name = "{package}"',
            f'version = "{version}"',
            f'description = "{description}"',
            f'requires-python = ">={floor}"',
            f'license = "{spdx}"',
            f'authors = [{{ name = "{author}" }}]',
            'dependencies = ["httpx>=0.27", "rich>=13"]',
            "",
            "[project.scripts]",
            f'{command} = "{package.replace("-", "_")}.cli:main"',
            "",
        ]
    )
    help_lines = [f"usage: {command} [OPTIONS] SOURCE", "", description, "", "options:"]
    help_lines.append(f"  {'-h, --help':<22}Show this message and exit")
    for f in flags:
        help_lines.append(f"  {f['flag']:<22}{f['help']}")
    help_text = "\n".join(help_lines) + "\n"
    license_line = f"{license_name}\n"

    data = {
        "package": package,
        "version": version,
        "description": description,
        "command": command,
        "requires_python": f">={floor}",
        "license": spdx,
        "flags": [{"flag": f["flag"], "default": f["default"]} for f in flags],
        "defaults": {"timeout_s": timeout, "workers": workers, "retries": retries},
    }
    truth = {
        "values": {"timeout_s": timeout, "workers": workers, "retries": retries, "python_floor": float(floor)},
        "labels": {
            "timeout_s": ["timeout"],
            "workers": ["workers"],
            "retries": ["retries"],
            "python_floor": ["Python"],
        },
        "derived": {},
        "facts": {
            "install_command": f"pip install {package}",
            "cli_command": command,
            "cli_flags": ", ".join(["-h/--help", *flag_names]),
            "license": spdx,
            "python_floor": f">={floor}",
            "version": version,
        },
    }
    attachments = {"pyproject.toml": pyproject, "cli_help.txt": help_text, "LICENSE": license_line}
    info = {"package": package, "command": command, "flags": flag_names, "extras": [], "env": [], "removed": None}
    fx = Fixture(entity=package, data=data, truth=truth, info=info, attachments=attachments)
    if extras:
        _add_extras(rng, fx)
    if env:
        _add_env(fx)
    if changelog:
        _add_changelog(rng, fx, version)
    return fx


EXTRAS = (
    ("s3", "boto3>=1.34"),
    ("gcs", "google-cloud-storage>=2.16"),
    ("azure", "azure-storage-blob>=12.19"),
    ("yaml", "pyyaml>=6.0"),
)
REMOVED_FLAGS = (
    ("--threads N", "--workers N"),
    ("--legacy-paths", "--config PATH"),
    ("--no-progress", "--verbose"),
    ("--max-attempts N", "--retries N"),
)


def _add_extras(rng: random.Random, fx: Fixture) -> None:
    pkg = fx.data["package"]
    chosen = sorted(rng.sample(EXTRAS, 2))
    block = ["[project.optional-dependencies]", *(f'{name} = ["{dep}"]' for name, dep in chosen), ""]
    fx.attachments["pyproject.toml"] += "\n".join(block)
    fx.data["extras"] = {name: dep for name, dep in chosen}
    fx.truth["facts"]["extras"] = ", ".join(name for name, _ in chosen)
    fx.truth["facts"]["install_with_extra"] = f'pip install "{pkg}[{chosen[0][0]}]"'
    fx.info["extras"] = [name for name, _ in chosen]


def _add_env(fx: Fixture) -> None:
    prefix = fx.data["command"].upper()
    env = [
        (f"{prefix}_TOKEN", "API token for the storage backend (required)"),
        (f"{prefix}_CACHE_DIR", "Where to keep the local state cache (default: ~/.cache)"),
    ]
    width = max(22, *(len(n) + 2 for n, _ in env))
    lines = ["", "environment variables:", *(f"  {name.ljust(width)}{text}" for name, text in env)]
    fx.attachments["cli_help.txt"] += "\n".join(lines) + "\n"
    fx.data["environment"] = [{"name": n, "help": h} for n, h in env]
    fx.truth["facts"]["env_vars"] = ", ".join(n for n, _ in env)
    fx.info["env"] = [n for n, _ in env]


def _add_changelog(rng: random.Random, fx: Fixture, version: str) -> None:
    current = set(fx.info["flags"])
    removed, replacement = rng.choice([r for r in REMOVED_FLAGS if r[0].split()[0] not in current])
    major, minor, _ = version.split(".")
    prev = f"{major}.{int(minor) - 1}.{rng.randint(1, 9)}"
    older = f"{major}.{int(minor) - 1}.0"
    cmd = fx.data["command"]
    text = "\n".join([
        "# Changelog",
        "",
        f"## {version}",
        "",
        f"- Removed `{removed}`, deprecated since {older}. Use `{replacement}` instead.",
        "- Added `--dry-run` to print what would happen without changing anything.",
        "- Fixed a crash when SOURCE contained a symlink loop.",
        "",
        f"## {prev}",
        "",
        f"- `{cmd}` now reads `./{cmd}.toml` by default.",
        f"- `{removed}` still works but prints a deprecation warning.",
        "",
        f"## {older}",
        "",
        f"- Deprecated `{removed}` in favour of `{replacement}`.",
        "",
    ])
    fx.attachments["CHANGELOG.md"] = text
    fx.truth["facts"]["removed_flags"] = removed.split()[0]
    fx.info["removed"] = removed.split()[0]


# ---------------------------------------------------------------- F6 release notes


FEATURES = (
    ("Resumable uploads", "Large uploads resume from the last confirmed chunk after a dropped connection."),
    ("Glob filters", "Include and exclude patterns accept shell-style globs."),
    ("JSON output", "Every command can print machine-readable JSON with a single option."),
    ("Parallel checksums", "Checksums for large files are computed in parallel."),
    ("Config profiles", "A config file can hold named profiles for different environments."),
    ("Progress bar", "Long operations show a progress bar with an estimated finish time."),
    ("Plugin hooks", "Plugins can register hooks that run before and after each file."),
)
FIXES = (
    "Fixed a crash when a file was deleted during a scan.",
    "Fixed incorrect exit codes when a remote path did not exist.",
    "Fixed a memory leak in the watcher on long-running sessions.",
    "Fixed Windows paths with trailing spaces being skipped.",
    "Fixed duplicate log lines when verbose output was redirected.",
    "Fixed time zone handling in the modified-since filter.",
    "Fixed a race that could leave a partial file after cancellation.",
    "Fixed the help text for the config option.",
)
DEPRECATIONS = (
    "The `--legacy-paths` option is deprecated and will be removed in the next minor release.",
    "The `sync_tree()` Python function is deprecated in favour of `Mirror.run()`.",
    "Reading settings from `setup.cfg` is deprecated; use the TOML config file.",
)
CONTRIBUTORS = (
    "Ines Varga", "Kenji Moreau", "Ama Brennan", "Dario Nwosu", "Tomasz Okafor", "Priya Halloran",
    "Mateo Lindqvist", "Sofia Achterberg", "Lena Castellanos", "Olu Petrakis",
)


def release(seed: int, *, missing: bool = False) -> Fixture:
    """Release notes for one version of a fictional package (F6: many short sections).

    Merged pull requests are grouped into features, fixes and deprecations, each with a PR
    number; one breaking change raises the Python floor. Three benchmarks give before/after
    figures.

    Traps (keys as in truth.json):
      T1 ``cold_start_improvement_pct`` and ``sync_time_improvement_pct`` are derivable, not
         given; the improvements in milliseconds and MB (simple differences) are allowed.
      T4 (``missing``) the peak-memory benchmark was not rerun: ``peak_memory_after_mb`` is null.
    """
    rng = random.Random(seed)
    base = rng.choice(NAMES)
    verb, description = rng.choice(PURPOSES)
    package = f"{base}-{verb}"
    major, minor = rng.randint(1, 4), rng.randint(2, 9)
    version = f"{major}.{minor}.0"
    previous = f"{major}.{minor - 1}.{rng.randint(1, 6)}"
    old_floor, new_floor = rng.choice((("3.9", "3.11"), ("3.9", "3.12")))  # 3.11 and 3.12 are too close to tell apart

    for _ in range(1000):
        cold_before = _odd_int(rng, 380, 940)
        cold_after = _odd_int(rng, int(cold_before * 0.55), int(cold_before * 0.85))
        sync_before = _odd_int(rng, 1200, 4800)
        sync_after = _odd_int(rng, int(sync_before * 0.5), int(sync_before * 0.82))
        mem_before = _odd_int(rng, 140, 690)
        mem_after = None if missing else _odd_int(rng, int(mem_before * 0.7), int(mem_before * 0.95))
        merged = _odd_int(rng, 31, 97)
        n_contrib = rng.randint(11, 19)
        values: dict[str, Any] = {
            "cold_start_before_ms": cold_before,
            "cold_start_after_ms": cold_after,
            "sync_time_before_ms": sync_before,
            "sync_time_after_ms": sync_after,
            "peak_memory_before_mb": mem_before,
            "peak_memory_after_mb": mem_after,
            "merged_prs": merged,
            "contributors": n_contrib,
            "python_floor_old": float(old_floor),
            "python_floor_new": float(new_floor),
        }
        derived: dict[str, dict[str, Any]] = {
            "cold_start_improvement_ms": {"value": cold_before - cold_after, "formula": "cold_start_before_ms - cold_start_after_ms"},
            "sync_time_improvement_ms": {"value": sync_before - sync_after, "formula": "sync_time_before_ms - sync_time_after_ms"},
            "cold_start_improvement_pct": {
                "value": round((1 - cold_after / cold_before) * 100, 1),
                "formula": "(1 - cold_start_after_ms / cold_start_before_ms) * 100",
            },
            "sync_time_improvement_pct": {
                "value": round((1 - sync_after / sync_before) * 100, 1),
                "formula": "(1 - sync_time_after_ms / sync_time_before_ms) * 100",
            },
        }
        if mem_after is not None:
            derived["peak_memory_improvement_mb"] = {"value": mem_before - mem_after, "formula": "peak_memory_before_mb - peak_memory_after_mb"}
        check = {k: v for k, v in values.items() if v is not None}
        check.update({k: d["value"] for k, d in derived.items()})
        if all(float(v) % 1 for k, v in check.items() if k.endswith("_pct")) and distinct(check):
            break
    else:  # pragma: no cover
        raise RuntimeError(f"seed {seed}: could not draw distinct figures")

    prs = rng.sample(range(1103, 2989), 12)
    people = rng.sample(CONTRIBUTORS, 6)
    features = [
        {"pr": prs[i], "title": t, "summary": s, "author": people[i % 6]}
        for i, (t, s) in enumerate(rng.sample(FEATURES, 4))
    ]
    fixes = [{"pr": prs[4 + i], "summary": s, "author": people[(i + 2) % 6]} for i, s in enumerate(rng.sample(FIXES, 5))]
    deprecations = [{"pr": prs[9 + i], "summary": s} for i, s in enumerate(rng.sample(DEPRECATIONS, 2))]
    breaking = [{
        "pr": prs[11],
        "summary": f"Python {old_floor} is no longer supported; {package} now requires Python {new_floor} or later.",
    }]
    benchmarks = [
        {"benchmark": "Cold start (ms)", previous: cold_before, version: cold_after},
        {"benchmark": "Sync of a large tree (ms)", previous: sync_before, version: sync_after},
        {"benchmark": "Peak memory (MB)", previous: mem_before, version: mem_after},
    ]
    notes = ["Benchmarks ran on the project's reference runner with the same test corpus for both versions."]
    if missing:
        notes.append("The peak-memory benchmark was not rerun for this release because the runner image changed.")
    data = {
        "package": package,
        "description": description,
        "version": version,
        "previous_version": previous,
        "requires_python": f">={new_floor}",
        "previous_requires_python": f">={old_floor}",
        "features": features,
        "fixes": fixes,
        "deprecations": deprecations,
        "breaking_changes": breaking,
        "benchmarks": benchmarks,
        "benchmark_notes": notes,
        "stats": {"merged_prs": merged, "contributors": n_contrib},
        "contributors_named": sorted(people),
        "metrics": values,
    }
    labels = {
        "cold_start_before_ms": ["cold start"],
        "cold_start_after_ms": ["cold start"],
        "sync_time_before_ms": ["sync"],
        "sync_time_after_ms": ["sync"],
        "peak_memory_before_mb": ["peak memory", "memory"],
        "peak_memory_after_mb": ["peak memory", "memory"],
        "merged_prs": ["pull requests", "PRs", "merged"],
        "contributors": ["contributors"],
        "python_floor_old": ["Python"],
        "python_floor_new": ["Python"],
    }
    truth = {
        "values": values,
        "labels": labels,
        "derived": derived,
        "facts": {
            "package": package,
            "version": version,
            "previous_version": previous,
            "python_floor": f">={new_floor}",
            "dropped_python": old_floor,
            "features": ", ".join(f["title"] for f in features),
        },
    }
    info = {"package": package, "version": version, "previous": previous, "n_features": len(features),
            "n_fixes": len(fixes), "n_deprecations": len(deprecations), "missing": missing}
    return Fixture(entity=package, data=data, truth=truth, info=info)
