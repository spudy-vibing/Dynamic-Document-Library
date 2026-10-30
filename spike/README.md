# DocCode API spike (WP-002)

A throwaway prototype for testing the authoring API with real users before the contract freeze (WP-003). It lives on branch `spike/api` and is **never merged**. Nothing here is the real library: it has no typing discipline, no error-path tests and no cache.

Findings and recommended BRD changes: [`docs/decisions/0004-api-spike.md`](../docs/decisions/0004-api-spike.md).
User-test kit: [`user-test/`](user-test/).

## Run it

You need [uv](https://docs.astral.sh/uv/) and Python 3.10 or later. From `spike/`:

```bash
uv run python run_examples.py        # plan + dry-run every example -> out/*.md, *.html, *.json
uv run --group dev pytest -q         # smoke tests (14)
uv run python -m doccode plan examples/02_quarterly_report.py
uv run python -m doccode run  examples/02_quarterly_report.py --dry-run -o out/q3.md -o out/q3.html
```

`python -m doccode` stands in for the `doccode` CLI. `--dry-run` swaps in `MockProvider`, reading `<script>.mock.json` when it exists (FR-CLI-3).

| Example | What it covers |
| --- | --- |
| `examples/00_quickstart.py` | The user-test quickstart: two sections, one f-string reference, `ctx` |
| `examples/01_weekly_status.py` (+ `.mock.json`) | UC-1: three independent sections, then "Next week" fans in from all three |
| `examples/02_quarterly_report.py` (+ `.mock.json`) | BRD §7, copied verbatim: subclass, `ctx`, `Value`, metrics, table, a helper prompt, checks, a conditional section and the `by-theme` expansion |

## What works

- `Document`, `doc.section(...)` as a context manager with a `ContextVar` stack, nesting, slug IDs, `brief=`, section `context=`, subclassing (FR-DOC-1..8, 13).
- `Prompt` with implicit registration into the open section, helper prompts (`visible=False` or created outside a section), generated IDs `<section>.<n>` and `_anon.<n>` (FR-PRM-1..5).
- Lazy references: `str()`/`format()` return `⟦doccode:ref:<key>⟧`; `ctx.name` returns `⟦doccode:ctx:name⟧`; `ref(max_chars=)`; token mode vs placeholder mode; single-pass resolution; misuse detection (format spec, `!r`, case changes, slicing) (FR-REF-1..5, 7..9, FR-CTX-1, 2, 4).
- `Value(fn, *deps)` run in a thread, value-backed `metric()` (FR-VAL-1..3).
- Graph from tokens, `depends_on`, `refs=` and check sources; cycle detection with the path; `doc.plan()` printing levels and flagging helpers and generated IDs; `plan.to_mermaid()` (FR-DAG-1..4, cut down).
- **Ready-queue execution** under `asyncio.Semaphore(max_concurrency)`, ties broken by document order; a test proves a slow prompt does not hold up an unrelated downstream prompt (FR-EXE-3). Sync `execute()` uses a worker thread inside a running loop, as in Jupyter (FR-EXE-1).
- **One expansion** (`doc.expand(build, after=..., id=...)`): runs at its position, lifts the freeze only inside the build function, validates new nodes, prefixes generated IDs (`by-theme/hiring`), enforces `max_nodes` and depth 3, and makes `depends_on=<expansion>` wait for the nodes it created (FR-DYN-1..5).
- `MockProvider`: dict, JSON path or callable, per-kind defaults, `len//4` tokens, unknown-key `ConfigError` with "did you mean", expansion-prefixed keys checked when the expansion runs (FR-PRV-4).
- The frame: outline frame in the shared prefix (expansion shown as "subsections generated at run time" except to its own prompts), position line, attachments as shared blocks, `OUTPUT_INSTRUCTION` + `style` as the system prompt (FR-COH-1, FR-PRV-7, FR-PRM-12, FR-DOC-12).
- Clean-up: heading shift, repeated-title heading removed, chatter trimmed (FR-OUT-1..3).
- Content model (frozen dataclasses), Markdown renderer, HTML through sandboxed Jinja2 + markdown-it-py with raw HTML disabled, JSON with provenance stripped by default (FR-CNT-1..3, 8; FR-RND-3, 4 partly).
- `OrphanPromptWarning` for a prompt made before the `with` block (FR-PRM-10).

## Shortcuts (everything the spike does not do)

| Area | Shortcut | BRD |
| --- | --- | --- |
| Providers | No `AnthropicProvider` by default: an unset provider raises `ConfigError` pointing at `--dry-run`. `doccode/live.py` is a ~40-line adapter for the owner's one live call, **untested by the builder** (no key held). No retries, rate limits, timeouts, refusals, prompt-cache breakpoints, pricing or cost. | FR-PRV-1..3, 5..8, FR-EXE-6, 11 |
| Cache and lock | None. `execute(lock=...)` is accepted and ignored with a warning, so §7 runs unchanged. | FR-CCH, FR-LCK |
| Budget | None. | FR-EXE-7 |
| Errors | `on_error="fail"` only (`"continue"` raises `ConfigError`). No `ErrorBlock` path. `ExecutionError` carries a partial record but there is nothing to resume from. | FR-EXE-4, 5 |
| Events | No `on_event`, no typed events. `RunRecord`/`PromptRecord` hold only a subset of fields. | FR-EXE-8..10 |
| Checks | `max_words`, `min_words`, `numbers_from`, `custom` exist and their sources become edges, but a failure is only a warning in the record: no retry, no `CheckFailedError`, no fingerprints in a cache key. | FR-OUT-4 |
| Coherence | Frame only. `coherence="report"` is accepted, but there is no `_brief`, editor, sequential flow or repetition detection. | FR-COH-2..6 |
| IDs | **Deviation:** section IDs live in their own namespace by default, because BRD §7 collides under FR-DAG-1 (finding F1 in 0004). `doccode._core.SHARED_NAMESPACE = True` restores the BRD rule (a test shows §7 then fails). | FR-DAG-1, FR-DOC-5 |
| Context | **Deviation:** a `Prompt`/`Value` passed in `context=` raises `TokenMisuseError` instead of being `str()`-ed into a raw token (finding F6). | FR-CTX-2 |
| Rendering | No PDF (`.pdf` raises `RenderError`), no template directories with `manifest.toml`, no `corporate`/`minimal`, no `nh3` or link policy (raw HTML is simply disabled in markdown-it). `template=` takes a `.j2` file or a directory holding `document.html.j2`. | FR-RND-4..10, NFR-26 |
| Content | No `from_json`, `diff`, `save_run`, JSON Schema or `toc()`; `link_hosts` always empty. Prose added to a section after a subsection renders before it (the BRD content model has `blocks` then `subsections`). | FR-CNT-4, 6, 7, 9 |
| Misc | No `doc.copy()`, `configure()` covers only `provider`, `context`, `model`; no env vars; no t-strings (FR-REF-6); no `output_schema`; no CLI beyond `run`/`plan`; no `mypy`/`ruff`; values are only nodes when reached (unclear in the BRD, see 0004). | FR-DOC-9, FR-CFG-1, FR-REF-6, FR-PRM-7, §4.11 |

## Owner's live call

```bash
cd spike
uv run --extra live python - <<'EOF'
import importlib.util, doccode
from doccode.live import AnthropicProvider
spec = importlib.util.spec_from_file_location("ex", "examples/01_weekly_status.py")
ex = importlib.util.module_from_spec(spec); spec.loader.exec_module(ex)
content = ex.build().execute(provider=AnthropicProvider())   # reads ANTHROPIC_API_KEY
content.save("out/weekly-live.md"); content.save("out/weekly-live.html")
print(open("out/weekly-live.md").read())
EOF
```

UC-1 is four calls on `claude-sonnet-5-5` (decision 0005), so a few cents. Record what you see in §"Owner's live call" of decision 0004. Watch whether the frame and position line make sections stay in scope, and whether the output needed clean-up.
