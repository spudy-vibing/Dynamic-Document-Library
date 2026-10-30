"""Generate a held-out case set from owner-held seeds (EVAL-6; EVALS_PROPOSAL §5.3). Owner only.

    uv run --project evals python -m evals.fixtures.heldout --seeds ~/doccode-heldout/seeds.txt \\
        --out ~/doccode-heldout/cases

The seeds file has the same format as ``seeds.dev.txt`` (``family seed options``) and lives
outside the repository; so does the output. The family builders are exactly the dev ones
(``make_cases.FAMILIES``), so a held-out case differs from a dev case only by its seed.

Guards, so held-out material cannot end up in the repository or in front of a builder:

* the command refuses a seeds file or an output directory inside the repository;
* case IDs are opaque (``h-f2-01``), so run manifests and committed summaries never carry a seed;
* it prints counts per family only, never seeds, IDs or content.

Run the set with the normal runner, pointing ``--case-set`` at the output directory
(an absolute path): ``python -m evals all --live --case-set ~/doccode-heldout/cases``.
Commit only the aggregate summary; rotate the seeds every release.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

from evals.core.config import EVALS_DIR
from evals.fixtures import make_cases

REPO_ROOT = EVALS_DIR.resolve().parent


class HeldoutError(SystemExit):
    pass


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def protected_roots(repo_root: Path = REPO_ROOT) -> list[Path]:
    """The repository root and every enclosing git checkout (a worktree sits inside the main one)."""
    root = repo_root.resolve()
    return [root, *(p for p in root.parents if (p / ".git").exists())]


def check_outside_repo(path: Path, what: str, repo_root: Path = REPO_ROOT) -> None:
    if any(_inside(Path(path).expanduser(), r) for r in protected_roots(repo_root)):
        raise HeldoutError(
            f"refusing: the held-out {what} must be outside the repository ({repo_root}); "
            "held-out seeds and cases are never committed (EVAL-6)"
        )


def generate(seeds: Path, out: Path, *, repo_root: Path = REPO_ROOT) -> Counter[str]:
    """Write the held-out cases for ``seeds`` into ``out``; returns case counts per family."""
    seeds, out = Path(seeds).expanduser(), Path(out).expanduser()
    check_outside_repo(seeds, "seeds file", repo_root)
    check_outside_repo(out, "output directory", repo_root)
    if not seeds.is_file():
        raise HeldoutError(f"seeds file not found: {seeds}")
    lines = make_cases.read_seeds(seeds)
    if not lines:
        raise HeldoutError("the seeds file has no seed lines")
    make_cases.write_all(out, seeds, opaque_ids=True)
    return Counter(family for family, _, _ in lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--seeds", type=Path, required=True, help="owner-held seeds file, outside the repository")
    ap.add_argument("--out", type=Path, required=True, help="output directory, outside the repository")
    args = ap.parse_args(argv)
    try:
        counts = generate(args.seeds, args.out)
    except ValueError as e:  # a bad seed line; the message names the family and option only
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(f"wrote {sum(counts.values())} held-out case(s): " + ", ".join(f"{f} {n}" for f, n in sorted(counts.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
