"""Route a bare ``pytest`` run from the repository root to the right suite (WP-001).

The root ``pyproject.toml`` holds the library's pytest settings, so pytest uses it for *every*
run started at the root, including ``uv run --project evals pytest`` (the eval harness's own
command, which must keep working unchanged). Both suites are in ``testpaths``; this file keeps
the one that does not belong to the active environment out of collection:

* library environment (``uv run pytest``): ``doccode`` is installed, so ``evals/`` is skipped.
  The offline eval job is WP-019's, not the library suite's.
* eval environment (``uv run --project evals pytest``): ``doccode`` is not installed (EVAL-8:
  the harness never depends on the library), so the library's ``tests/`` are skipped.

Explicit paths still work: ``uv run pytest tests/test_smoke.py``.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_ROOT = Path(__file__).parent
_SKIP = _ROOT / ("evals" if importlib.util.find_spec("doccode") is not None else "tests")


def pytest_ignore_collect(collection_path: Path) -> bool | None:
    """Skip the other environment's suite; defer to pytest's defaults for everything else."""
    if collection_path == _SKIP or _SKIP in collection_path.parents:
        return True
    return None
