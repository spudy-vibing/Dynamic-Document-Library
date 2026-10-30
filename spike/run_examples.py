"""One command for the spike: `uv run python run_examples.py` (from spike/).

Prints each example's plan, then dry-runs it with MockProvider (+ <script>.mock.json) into out/.
"""

from __future__ import annotations

import sys
from pathlib import Path

from doccode.__main__ import main

HERE = Path(__file__).parent
EXAMPLES = ["00_quickstart.py", "01_weekly_status.py", "02_quarterly_report.py"]

if __name__ == "__main__":
    rc = 0
    for name in EXAMPLES:
        path = HERE / "examples" / name
        stem = path.stem
        print(f"\n=== {name} ===")
        rc |= main(["plan", str(path)])
        rc |= main(["run", str(path), "--dry-run",
                    "-o", str(HERE / "out" / f"{stem}.md"),
                    "-o", str(HERE / "out" / f"{stem}.html"),
                    "-o", str(HERE / "out" / f"{stem}.json")])
    sys.exit(rc)
