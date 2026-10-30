# DocCode evals

The eval harness for DocCode (BRD §5.7, EVAL-1..24; design in `docs/EVALS_PROPOSAL.md`).
It is built before the library. It measures simple baseline ways of generating multi-section
documents and publishes a scoreboard that DocCode has to beat. DocCode later plugs in as one
more generator through the same `Generator` protocol. Nothing here imports `doccode` except
the future DocCode adapter (EVAL-8).

Everything runs **offline by default**. A FAKE run uses a deterministic `FakeLLM`, needs no
key and makes no network calls. Only the owner runs live.

## Layout

```
evals/
  config.toml           pinned models, explicit effort, prices, judge version/pairs, run caps
  core/                 the shared contract: types, case loader, neutral rendering, config
    llm.py              AnthropicLLM (the EVAL-21 wrapper) and FakeLLM
    storage.py          run directories, manifest, JSONL, resume
  cases/dev/            the dev cases (case.toml + data.json + truth.json + attachments)
  fixtures/             seeded synthetic data generators for the cases
  generators/           b0_oneshot.py, b1_parallel.py, b2_chain.py (+ n0_null.py), common.py
    prompts/*.md        FROZEN, versioned baseline prompts (see "Baseline prompts" below)
  graders/              deterministic graders (ALL_GRADERS)
  judge/pairwise.py     pairwise judge; rubric in judge/rubrics/pairwise.md
  runner.py             projection, generation, grading, judging
  report.py compare.py  report.md + scoreboard summary; run-to-run comparison
  scoreboard/           committed run summaries (<run_id>.summary.json, <run_id>.md)
  runs/                 gitignored: one directory per run
  tests/                pytest, no network (conftest blocks sockets)
```

A run directory `evals/runs/<run_id>/` holds `manifest.json` (git SHA, generator names and
versions, judge_version, models, effort, case IDs and case-set hash, mode, timestamps),
`docs/<generator>/<case>/<rep>.md` and `.json` (the full `GenerationResult`), `calls.jsonl`
(every model call), `errors.jsonl` (infra errors and refusals, never scored), `scores.jsonl`
(one row per generator, case, rep and metric), `judge.jsonl` (one row per judged order of a
pair) and `report.md`.

## Running offline (no key)

From the repository root:

```sh
uv run --project evals pytest evals            # the test suite
uv run --project evals python -m evals all     # FAKE run -> grade -> judge -> report
```

(Pass the `evals` path to pytest, or use `uv run --project evals python -m pytest`, so pytest
picks up `evals/pyproject.toml` and its `pythonpath`.)

The report of a FAKE run carries a "FAKE MODE — not a real result" banner. Its numbers only
show that the pipeline works.

## Commands

All commands run from the repository root as `uv run --project evals python -m evals <command>`.

| Command | What it does |
|---|---|
| `project [--generators ...] [--cases ...] [--reps N]` | Cost projection for a planned run: expected (from `[projection]`) and worst case (`max_tokens` on every call), generation and judging, against `run.max_usd` |
| `run [--live | --cli] [--generators ...] [--cases ...] [--reps N] [--run-id ID]` | Generate documents. FAKE unless `--live`. Rerunning the same `--run-id` resumes: completed slots are skipped, errored slots retried |
| `grade [--run-id ID]` | Apply every grader in `evals.graders.ALL_GRADERS` to every stored document -> `scores.jsonl` |
| `judge [--run-id ID] [--live | --cli]` | Pairwise judge over `judge.pairs` -> `judge.jsonl` (resumable) |
| `report [--run-id ID]` | `runs/<id>/report.md` and `scoreboard/<id>.summary.json` + `.md` |
| `compare RUN_A RUN_B [--fail-on-regression]` | Paired per-metric deltas (B − A) with cluster-bootstrap CIs over cases; flags regressions |
| `all [--live | --cli] [run options]` | run -> grade -> judge -> report |

`grade`, `judge` and `report` default to the most recent run. Defaults for generators are
B0, B1 and B2; cases, reps and the case set come from `config.toml` (`[run]`).

## Live runs (owner only)

Agents never hold the API key (EVAL-18). The owner runs live from a local shell or an
owner-approved CI environment:

```sh
ANTHROPIC_API_KEY=... uv run --project evals python -m evals project
ANTHROPIC_API_KEY=... uv run --project evals python -m evals all --live
```

`--live` refuses to start without `ANTHROPIC_API_KEY`, prints the projection, and refuses if
the expected cost exceeds `run.max_usd` (EVAL-20). `judge --live` refuses a FAKE run, and a
live run cannot be judged by the fake judge. If a live run is interrupted, rerun the same
command with `--run-id <id>`: finished documents are kept, and a judge batch that was already
submitted is collected rather than paid for again.

**Do not commit live run data.** `evals/runs/` is gitignored. Only the summaries under
`evals/scoreboard/` are committed (FAKE summaries, `fake-*`, are ignored there too). Held-out
runs commit aggregate summaries only.

## Unofficial runs through Claude Code (`--cli`, no key)

`--cli` sends every generator and judge call through headless Claude Code (`claude -p`) on
the owner's subscription, so no API key is needed and nothing is billed per token (it uses
the subscription's usage allowance). Run IDs start with `cli-`.

```sh
uv run --project evals python -m evals all --cli --reps 1
```

Each call replaces Claude Code's system prompt with the eval's (`--system-prompt`), turns
tools off (`--tools ""`), loads no settings, MCP servers or slash commands, runs in an empty
temporary directory, sets `--model` and `--effort` from `config.toml`, sets `max_tokens`
through `CLAUDE_CODE_MAX_OUTPUT_TOKENS`, and strips `ANTHROPIC_API_KEY` from the child
environment. The served model is still asserted and refusals are still error rows.

It is **not** equivalent to `--live`, so its results never count toward a gate:
- Claude Code adds some context of its own to every call (a few hundred to about a thousand tokens).
- B2's chat history is flattened into one user message (headless mode takes one prompt per call).
- Prompt caching follows Claude Code's own breakpoints; refusal categories are not reported.
- `cost_usd` is the API-equivalent price of the reported tokens, not a bill; the judge runs
  one call per order (no Batches).

Use it to check the prompts, graders and judge rubric on real model text before paying for a
live run. `judge --cli` only accepts a `cli-` run; `cli-*` summaries are gitignored.

## What costs what

Prices per million tokens are pinned in `config.toml` (verify them before a live run).

| Item | Calls |
|---|---|
| B0 one-shot | 1 per document |
| B1 naive parallel | 1 per section, concurrent where dependencies allow |
| B2 chat chain | 1 per section, each resending the whole conversation so far |
| Pairwise judge | 2 per pair (both orders); through the Message Batches API at 50% when `judge.use_batches` |

The MVP run (6 dev cases × 3 reps × B0/B1/B2 + 36 pairs judged in both orders) projects at
about **$6.70 expected, $40 worst case**, under the $15 cap. `python -m evals project` prints
the current numbers. Every report splits the measured cost into generation and judging,
computed from API usage (`calls.jsonl`); judge costs from Batches include the 50% discount.

## How the model is called (EVAL-21)

`core/llm.py` is the only place that calls the API:

- `output_config={"effort": ...}` is always sent; `temperature`, `top_p`, `top_k`,
  `thinking`, `fallbacks` and betas are never sent, and no SDK middleware is registered.
- Requests stream (`messages.stream(...)` then `get_final_message()`); only `text` blocks
  become the output.
- The served model must equal the requested model; a mismatch is an infra error.
- `stop_reason: "refusal"` is recorded with `stop_details.category`, retried once, and a second
  refusal leaves the slot unscored (an error row). Refusal rate above `run.refusal_rate_limit`
  marks the generator "not comparable" in the report.
- 429, 529, 5xx, timeouts and connection errors are retried with backoff by the wrapper (the
  SDK's own retries are off, so every attempt is counted in `CallRecord.attempts`).
- Baselines put the case data and attachments first, in one byte-stable block with a
  `cache_control` breakpoint, so B1 and B2 can read it from the provider's prompt cache.
  Prompts below the model's minimum cacheable size simply do not cache. Reps of the same
  case that run within the cache lifetime can also read each other's prefix; that affects
  cost and cache-hit numbers only, never the text.

## Baseline prompts (owner review)

The baselines are "what a competent developer writes in an afternoon" (EVAL-3). Their prompts
live in `evals/generators/prompts/`:

- `_shared.md`: system prompt, the data/attachments block, the outline entry, the fact rules
- `b0_oneshot.md`, `b1_parallel.md`, `b2_chain.md`

Each file has a version line. A generator's version includes each prompt file's version and a
hash of its content (for example `1+b0_oneshot@1-b3ffbb40+_shared@1-f16746ea`), so any edit
changes the version recorded in the manifest. The owner reviews and freezes them before any
DocCode result exists; after that, change them only with owner approval and a version bump.
Baseline output is never cleaned up: the harness only inserts the data blocks (origin
`data`) at the start of each section and keeps the model's text as written.

The judge rubric is `evals/judge/rubrics/pairwise.md`; `judge_version` is `judge.version` from
`config.toml` plus a hash of that file. Results with different judge versions are never
compared (EVAL-12).
