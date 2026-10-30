# DocCode

**Write documents like code.** DocCode is a Python library for building long, multi-section documents with LLMs. You define the document in Python — sections, prompts, data tables, references between sections — and DocCode runs it as a dependency graph, keeps the sections coherent, checks the numbers against your data, caches and locks the outputs, and renders Markdown, HTML or PDF from templates.

> **Status: pre-alpha, design and evaluation stage.** The library itself is not written yet. This repository holds the requirements, the plan, the design, and an evaluation harness that is built *before* the library so that every feature is measured against simple baselines.

## What it will look like

```python
from doccode import Document, Prompt, checks

data = {"revenue": 50_000_000, "prior_revenue": 40_000_000, "kpis": [...]}

doc = Document("Acme Q3 2026 Report", purpose="Quarterly update for the leadership team.",
               coherence="report")
doc.attach("data", data)
grounded = checks.numbers_from(data)                 # flag figures that are not in the data

with doc.section("Executive Summary") as s:
    s.table(data["kpis"], caption="Key metrics")     # tables come straight from the data
    summary = Prompt("Write a 2-paragraph summary of the quarter.", id="summary", checks=[grounded])

with doc.section("Recommendations"):
    Prompt(f"Given {summary}, give 3 recommendations for next quarter.", id="recommendations")

content = doc.execute()                              # runs the graph; cached and lockable
content.save("q3.md")
content.save("q3.pdf", template="corporate")
```

The full reference example is in the BRD, §7.

## Ideas in one paragraph each

- **Lazy references.** A prompt used inside an f-string (`{summary}`) becomes a token that is filled in once the upstream prompt has run, so prompts read like plain Python.
- **A DAG, not a chat.** Independent sections run in parallel; dependent ones wait only for what they reference. Expansions let a prompt's output (an outline, say) create new sections at run time.
- **Coherence layers.** An outline frame, a shared brief, optional sequential flow and an editor pass keep separately written sections reading as one document. Presets: `basic`, `report`, `narrative`, `off`.
- **Numbers you can trust.** Data blocks render tables straight from your data, and `checks.numbers_from(...)` flags figures the model made up.
- **Reproducible.** A cache and a committed lockfile mean an unchanged document makes zero model calls and renders byte-identical output in CI.

## Repository layout

| Path | What is there |
| --- | --- |
| `docs/DocCode BRD Write Documents Like Code.md` | Requirements (FR/NFR/EVAL IDs), scope of 0.1.0 |
| `docs/DEVELOPMENT_PLAN.md` | Multi-agent build plan: waves 0–5, work packages, gates |
| `docs/HLD.md` | High-level design |
| `docs/EVALS_PROPOSAL.md` | Evaluation design |
| `docs/STATUS.md` | **What is done and what is left** — start here |
| `docs/decisions/` | Decision records |
| `evals/` | Evaluation harness, test cases, baselines, graders, judge |

## Running the evals

Requires [uv](https://docs.astral.sh/uv/). From the repository root:

```sh
uv run --project evals pytest                       # tests, fully offline
uv run --project evals python -m evals all          # fake mode: no key, no network
uv run --project evals python -m evals all --cli    # unofficial: real models via Claude Code, no API key
ANTHROPIC_API_KEY=... uv run --project evals python -m evals all --live   # official, billed (cap in evals/config.toml)
```

See `evals/README.md` for the commands, what each mode costs and what it does and does not prove.

## Developing the library

The library lives in `src/doccode/` (currently only the scaffold). It is published on PyPI
as `doccode-py` (`docs/decisions/0002-package-name.md`); the import name and the CLI are
`doccode`.

```sh
uv run pytest          # library tests, fully offline (sockets are blocked)
make check             # lint, format, mypy --strict, tests + coverage, ownership, traceability
```

## License

MIT. See `LICENSE`.
