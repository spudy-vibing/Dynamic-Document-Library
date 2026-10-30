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


def repo(seed: int) -> Fixture:
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
    info = {"package": package, "command": command, "flags": flag_names}
    return Fixture(entity=package, data=data, truth=truth, info=info, attachments=attachments)
