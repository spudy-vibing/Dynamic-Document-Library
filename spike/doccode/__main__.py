"""Tiny CLI stand-in for FR-CLI-1/3: `python -m doccode run|plan <file.py> [--dry-run] [-o out]`.

Not Typer/Rich. `run` without --dry-run uses whatever provider the script configures; with
--dry-run it uses MockProvider with `<script>.mock.json` when present (FR-CLI-3).
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
import time
from pathlib import Path

from ._core import Document
from .errors import ConfigError, DocCodeError, ExecutionError, GraphError
from .providers import default_mock_for


def load(path: Path, entry: str | None) -> Document:
    spec = importlib.util.spec_from_file_location(path.stem.replace("-", "_"), path)
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(path.parent))
    spec.loader.exec_module(mod)
    if entry:
        obj = getattr(mod, entry)
        return obj() if callable(obj) and not isinstance(obj, Document) else obj
    if isinstance(getattr(mod, "doc", None), Document):
        return mod.doc
    if callable(getattr(mod, "build", None)):
        return mod.build()
    print(f"error: {path} has no module-level `doc` and no zero-argument build()", file=sys.stderr)
    raise SystemExit(2)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m doccode")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("run", "plan"):
        p = sub.add_parser(name)
        p.add_argument("file", type=Path)
        p.add_argument("--entry")
        if name == "run":
            p.add_argument("-o", "--output", action="append",
                           help="output path(s); format from the extension (.md/.html/.json)")
            p.add_argument("-t", "--template")
            p.add_argument("--dry-run", action="store_true")
            p.add_argument("-j", type=int, default=4, dest="jobs")
        else:
            p.add_argument("--mermaid", action="store_true")
    a = ap.parse_args(argv)
    try:
        doc = load(a.file, a.entry)
        if a.cmd == "plan":
            plan = doc.plan()
            print(plan.to_mermaid() if a.mermaid else plan)
            return 0
        provider = default_mock_for(a.file) if a.dry_run else None
        t0 = time.perf_counter()
        content = doc.execute(provider=provider, max_concurrency=a.jobs)
        outs = a.output or [Path(a.file).with_suffix(".md").name]
        for o in outs:
            content.save(o, template=a.template if str(o).endswith((".html", ".htm")) else None)
        run = content.run
        print(f"{len(run.prompts)} nodes ok, {run.input_tokens}+{run.output_tokens} tokens "
              f"(mock estimate), {time.perf_counter() - t0:.2f}s -> {', '.join(map(str, outs))}")
        return 0
    except GraphError as e:
        print(f"validation error: {e}", file=sys.stderr)
        return 2
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 3
    except ExecutionError as e:
        print(f"run failed: {e}", file=sys.stderr)
        return 1
    except DocCodeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
