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
  fixtures/             seeded synthetic data generators for the cases, make_cases.py, seeds.dev.txt,
                        heldout.py (owner-held seeds, never in the repo)
  generators/           b0_oneshot.py, b1_parallel.py, b2_chain.py (+ n0_null.py), common.py
                        p1_frame.py .. p4_editor.py + coherence.py: the coherence prototypes (WP-017)
    prompts/*.md        FROZEN, versioned baseline prompts (see "Baseline prompts" below);
                        p1_frame.md .. p4_editor.md are the prototypes' candidate built-in text
  graders/              deterministic graders (ALL_GRADERS)
  judge/pairwise.py     pairwise judge; rubric in judge/rubrics/pairwise.md
  calibration/          judge calibration against human labels (WP-018): packs, label import, agreement
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

## The dev set (EVAL-6, EVAL-7)

24 committed cases over seven families, each a pure function of one line of
`fixtures/seeds.dev.txt` (`family seed options`). Fictional entities, distinctive figures, and
every trap recorded in `truth.json` and the case's `[traps]`.

| Family | Cases | Generator | Traps |
|---|---|---|---|
| F1 weekly status | 4 | `synth_company.weekly` | T1 (totals across teams), T4 (option `missing`) |
| F2 quarterly report | 6 | `synth_company.quarterly` | T1 growth rate, T2, T3 (`unit`), T4, T5 (`bad_news`), T6 (`yoy`); `themes` = expansion-style summary |
| F3 README | 4 | `synth_repo.repo` | facts; removed CLI flag (`changelog`); `extras`, `env` add install and configuration facts |
| F4 annual review | 3 | `synth_company.annual` | T1 year growth, T2 (Q2/Q3 margins), T4, T5 (H2 decline), T6 (`yoy`: Q3 vs Q4 last year) |
| F5 incident postmortem | 3 | `synth_incident.incident` | T1 failed-request share, T3 (`unit`: SLA credits), T4 (`missing`), T5 (always) |
| F6 release notes | 2 | `synth_repo.release` | T1 % improvements, T4 (`missing` benchmark); seven short sections |
| F7 robustness | 2 | `synth_company.robust`, `synth_feedback.support_review` (`long`) | bad-news quarter with two missing KPIs and an injected data note (T1–T5, T7); a 110k-token ticket export with a buried critical ticket and an injected ticket (T7) |

Allowed arithmetic follows the owner's rule (2026-10-30): sums and differences of given
figures (quarter-to-quarter and year-on-year changes, full-year totals, minutes between
incident events, benchmark savings in ms) are in `allowed_derived`; percentage rates computed
from given figures stay T1 traps. Figures stated in an attachment count as supported, so the
long attachment contains no figures except the injected claim.

```sh
uv run --project evals python -m evals.fixtures.make_cases          # regenerate evals/cases/dev
uv run --project evals python -m evals.fixtures.make_cases --check  # CI: exit 1 on any difference
```

Each seed line may only use its family's options (`make_cases.FAMILY_OPTIONS`); a typo fails.
Never edit a committed seed line: append new ones. Options are drawn after a seed's default
figures, so adding an option to the generator does not change existing cases.

**Trap graders added in WP-016**

- `trap.t6_period` (`graders/numbers.py`): a figure equal to one comparison base (prior quarter
  or same quarter last year) stated with the other base's period label as its nearest label
  in the sentence. Uses truth `periods` and `period_of`. Heuristic; the claim checker owns the
  full judgement.
- `trap.t7_instruction`, `trap.t7a_link`, `trap.t7b_link` (`graders/links.py`): the injected
  claim's figure appears; injected URLs are clickable `<a href>` in the rendered HTML. Baselines
  are rendered by markdown-it-py (CommonMark plus GFM-style bare-URL autolinks, no link policy);
  a result that carries its own `html` is graded on that. T7a is reported, never gated (NFR-26
  allows hosts from the inputs); T7b must be 0 for DocCode (EVAL-24).
- `trap.readme_flags` (`graders/flags.py`): a README presents a removed flag as current, or
  invents a flag. Heuristic; lines running pip, uv, git and similar are skipped.

## Held-out set (owner only, EVAL-6)

Held-out cases come from the same family builders, run on seeds that only the owner holds,
outside the repository, rotated every release. Builder agents never see the seeds, the cases,
the documents or per-case scores.

```sh
uv run --project evals python -m evals.fixtures.heldout \
    --seeds ~/doccode-heldout/seeds.txt --out ~/doccode-heldout/cases
uv run --project evals python -m evals all --live --case-set ~/doccode-heldout/cases
```

- The seeds file uses the `seeds.dev.txt` format. Match the dev mix: F1 4, F2 6, F3 4, F4 3,
  F5 3, F6 2, F7 2 (one F7 line with `long`).
- The command refuses a seeds file or an output directory inside the repository (or any
  enclosing checkout), and prints only per-family counts.
- Case IDs are opaque (`h-f2-01`), so manifests and summaries never carry a seed.
- `evals/.gitignore` also ignores `cases/heldout/`, `heldout/` and held-out seed file names, in
  case something is copied in by hand.
- Run documents land in the gitignored `evals/runs/`; delete that run directory after
  reporting, and commit only the aggregate summary.

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
| `judge [--run-id ID] [--live | --cli] [--only pairwise absolute claims]` | Pairwise judge over `judge.pairs` -> `judge.jsonl`; absolute rubric -> `absolute.jsonl`; claim checker -> `claims.jsonl` (all resumable; default: every judge enabled in `config.toml`) |
| `report [--run-id ID]` | `runs/<id>/report.md` and `scoreboard/<id>.summary.json` + `.md`; a "Coherence ablation" section when the run has prototypes |
| `compare RUN_A RUN_B [--fail-on-regression]` | Paired per-metric deltas (B − A) with cluster-bootstrap CIs over cases; flags regressions |
| `all [--live | --cli] [run options]` | run -> grade -> judge -> report |
| `label export [--run-id ID] [--seed S] [--pairs N] [--documents N] [--out DIR]` | Sample a blinded labelling pack from a run (see "Judge calibration") |
| `label import FILE [--labeller ID] [--replace] [--allow-missing]` | Validate one labeller's labels (JSON from the page, or the filled-in CSV) and store them in the run |
| `calibrate [--run-id ID] [--recalibration] [--labellers ...] [--fail-on-miss]` | Judge-vs-human and human-vs-human agreement against the EVAL-13 thresholds -> `calibration.md`/`.json`; `report` then adds a calibration section |

`grade`, `judge` and `report` default to the most recent run. Defaults for generators are
B0, B1 and B2 (the prototypes P1-P4 are selected explicitly, see below); cases, reps and the
case set come from `config.toml` (`[run]`).

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

## CI (WP-019)

Two workflows, separate from the library's `ci.yml`. The root `conftest.py` keeps the eval
suite out of the library's `pytest` run, so nothing runs twice.

**`evals-offline.yml`** runs on every pull request and every push to `master` (and `main`).
It holds no secret and never calls a model: read-only token, FakeLLM only, uv cached on
`evals/uv.lock`. Steps:

1. `uv run --project evals pytest`: graders, perturbations, null baseline, schema, judges,
   and the workflow tests (`tests/test_ci_workflows.py`).
2. `python -m evals.checks.check_imports`: nothing outside the adapter imports `doccode` (EVAL-8).
3. `python -m evals.fixtures.make_cases --check`: the dev cases regenerate from their seeds (EVAL-7).
4. A FAKE `python -m evals all` on four cases × 1 rep × B0, B1, B2, P1, P4, run twice and
   compared with `--fail-on-regression` (the fake pipeline must be deterministic). The report
   and compare output are uploaded as the `evals-offline-smoke` artifact.

There is no committed FAKE baseline: FAKE summaries are not results (and `fake-*` is
gitignored). Run the same thing locally with `make evals` (it is not part of `make check`,
which is the library gate).

**`evals-live.yml`** is the owner-approved live run (EVAL-18, EVAL-20). It starts only from
**Actions → evals-live → Run workflow** (`workflow_dispatch`): never on push or pull request,
so a fork's PR cannot reach it, and the job is skipped in any repository other than
`spudy-vibing/Dynamic-Document-Library`. Inputs:

| Input | Default | Notes |
|---|---|---|
| `case_set` | `dev` | Only `dev`. Held-out runs stay on the owner's machine (EVAL-6). |
| `cases` | 6 dev cases, one per family F1–F6 | Space-separated IDs; empty = the whole case set |
| `generators` | `b0-oneshot b1-parallel` | Space-separated names |
| `reps` | `1` | 1–3 |
| `max_usd` | `5` | The effective cap is the lower of this and `run.max_usd` in `config.toml`; raising the cap above `config.toml` needs a PR that changes `config.toml` |

The defaults are the smoke run (about $2.30 expected). The job waits for the owner's approval,
then: validates the inputs → `python -m evals project` → refuses to start if the expected cost
exceeds the cap → `python -m evals all --live --run-id live-gh-<run>-<attempt>` (which projects
and checks `run.max_usd` again) → writes `report.md` to the job summary → uploads
`evals-report-<run_id>` (report, manifest, scoreboard summary; 90 days) and `evals-run-<run_id>`
(the whole run directory; 14 days). `ANTHROPIC_API_KEY` is passed only to the live step. The
token is read-only, so the workflow cannot commit: to publish a scoreboard summary, download
`evals-report-<run_id>` and commit its `<run_id>.summary.json` and `<run_id>.md` under
`evals/scoreboard/` in a normal PR.

**One-time setup (owner).** In the repository on GitHub:

1. **Settings → Environments → New environment**, name it exactly `eval-live`.
2. In `eval-live`, tick **Required reviewers** and add yourself (the owner). Optionally untick
   "Allow administrators to bypass" and, under **Deployment branches and tags**, choose
   **Selected branches and tags** and add `master`, so live runs only use reviewed code.
3. In `eval-live`, under **Environment secrets**, add `ANTHROPIC_API_KEY` with your key. Do
   not add it as a repository or organisation secret: an environment secret is released only
   to jobs that the environment has approved.
4. **Settings → Actions → General**: keep "Require approval for all outside collaborators"
   (the default for forks) on, and leave the default `GITHUB_TOKEN` permission at read.

To run: **Actions → evals-live → Run workflow**, pick the branch and inputs, then approve the
pending deployment to `eval-live` when GitHub asks. Spend goes on the wave's line in
`docs/STATUS.md` (plan §0.8).

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
| P1 frame | 1 per section, as B1 |
| P2 shared brief | 1 brief + 1 per section |
| P3 sequential flow | 1 per section, strictly in order, each prefix holding the document so far |
| P4 brief + editor | 1 brief + 1 per section + 1 editor (2 if its JSON needs the one retry) |
| Pairwise judge | 2 per pair (both orders); through the Message Batches API at 50% when `judge.use_batches` |
| Absolute rubric | 3 judge-model calls per document (Batches when live) |
| Claim checker | 1 claims-model (Sonnet) call per document (Batches when live) |

The MVP run (6 dev cases × 3 reps × B0/B1/B2 + 36 pairs judged in both orders, plus the
absolute rubric and claim check on all 54 documents) projects at about **$10.80 expected**
(about $3.25 absolute rubric and $0.80 claim check on top of $6.70), under the $15 cap. The full
dev set (24 cases × 3 reps) projects well above the cap, so a full live run needs the cap raised
by the owner (or a run split with `--cases`). The long-attachment case `x-calderwyn-support-8574`
alone is about $5.50 of generation: every one of its calls carries a 110k-token prompt (the
projection assumes no prompt-cache discount).
`python -m evals project` prints the current numbers. Every report splits the measured cost into generation and judging,
computed from API usage (`calls.jsonl`); judge costs from Batches include the 50% discount.

The same MVP size with the prototypes (B0, B1, B2, P1-P4: 126 documents, 10 judge pairs)
projects at about **$24 expected, $147 worst case** (generation $15.20, of which the
prototypes $10.30; judging $9). That is above the $15 cap, so the owner raises
`run.max_usd` for that run or runs the prototypes separately (`--generators b0-oneshot
b1-parallel p1-frame p2-brief p3-flow p4-editor`: about $21 expected). In `--cli` mode the
judge runs without Batches, so the API-equivalent figure is about $33.

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

## Coherence prototypes P1-P4 (WP-017, EVAL-4)

Plain-Python prototypes of DocCode's coherence layers (BRD FR-COH-1..5), each B1 plus one
layer. They never import `doccode`. Their prompt text is the candidate for DocCode's
built-in frame wording, brief instruction and editor prompt; the G0 ablation (WP-020,
decision 0006) uses them to choose the default preset per family and to tune that text. The
ablation never removes a layer by itself.

| Generator | Adds | Approximates | Call pattern |
|---|---|---|---|
| `p1-frame` | frame: title, purpose, audience, style and outline (titles and briefs) plus the no-repeat line in the shared prefix; a position line per section | `basic` | as B1 |
| `p2-brief` | P1 + shared brief: `_brief` runs first (Core message, Section points, Terms, Avoid) and goes into every section's prefix | `report` without the editor | 1 + sections |
| `p3-flow` | P1 + sequential flow: document order, "the document so far" appended to the prefix, oldest sections cut to their first paragraph above 60,000 characters | `narrative` without the editor | sections, serial |
| `p4-editor` | P2 + editor: one `_editor` call returns JSON find/replace edits; applied only when `find` matches exactly once in model text, data blocks untouched, change ratio ≤ 0.3, checks still pass | `report` | 1 + sections + 1 |

All four run repetition detection (word 8-grams, threshold 2, FR-COH-5) and record the
findings as warnings; P4 passes them to the editor and checks again after editing. Applied
edits, rejected or reverted edits and editor notes are recorded in the result's warnings.

**Request layout (FR-PRV-7).** `system` is the baselines' system prompt. The first user
message is: frame, data and attachments, the brief (P2/P4), the document so far (P3, one
block per earlier section), then the per-prompt text (position line and instruction). At
most two `cache_control` breakpoints: on the last static shared block, and (P3) on the end
of the document so far. The brief call's prefix is a prefix of every section's, and the
editor reuses the sections' prefix, so both can read the provider's cache.

Run them, and get the ablation, with:

```sh
uv run --project evals python -m evals all --generators b0-oneshot b1-parallel p1-frame p2-brief p3-flow p4-editor
```

The report's **Coherence ablation** section shows, for all cases and for each family: the
metric values for B1, B0 and each prototype; each prototype's delta against B1 and against
B0; the per-layer increments (frame = P1 − B1, brief = P2 − P1, flow = P3 − P1, editor =
P4 − P2); and the pairwise win rates of each prototype against B1 and B0 (pairs in
`judge.pairs`). The same data is in the scoreboard summary under `ablation`.

In FAKE mode the `FakeLLM` writes neither a valid brief nor editor JSON, so P2 and P4 run
with brief warnings and an unedited draft; the tests cover the real paths with scripted
replies.

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

## Absolute rubric and claim checker (EVAL-10, EVAL-11)

- **Absolute rubric** (`judge/absolute.py`, rubrics `judge/rubrics/absolute_common.md` and
  `absolute_{a,b,c}.md`): six anchored 1–5 criteria in three grouped judge calls per document
  (a: C1 accuracy vs data, C2 coherence; b: C3 no repetition, C4 formatting; c: C5 tone vs the
  style guide, C6 instruction adherence). Every score cites evidence; unparseable or
  out-of-range replies are `parse_error` rows. Floors and diagnosis only: the report shows the
  share of documents at or below `absolute.floor` (2) on any criterion and documents with C1 = 1
  (BRD §1.5); `compare` flags those, never the per-criterion means. Version:
  `judge.version` + a hash of every `absolute_*.md` file.
- **Claim checker** (`judge/claims.py`, prompt `judge/rubrics/claims.md`): `models.claims`
  (Sonnet, `effort.claims`) extracts every quantitative or comparative claim from the prose;
  the harness then re-checks each claim against `truth.json` deterministically wherever it names
  a truth key with a figure or direction (misattributed, wrong period, wrong direction, wrong
  value); facts and unmapped claims keep the model's verdict. Metrics: `misattrib`,
  `misattrib_silent`, `docs_misattrib_silent` (gate ≤ 1% of docs). Version: `claims.version` +
  the prompt hash. In FAKE mode the "model" is the deterministic label-based `rule_claims`.
- Both use the shared engine `judge/perdoc.py`: the `LLM` wrapper (fake, `--cli`, live) or the
  Message Batches API when live with `judge.use_batches`, refusals retried once and recorded as
  error rows, resumable by `custom_id`. Switch either off with `absolute.enabled` / `claims.enabled`.

## Judge calibration (WP-018, EVAL-13)

Before the judge counts at a gate, it must agree with humans (EVALS_PROPOSAL §6.3, BRD EVAL-13):
**≥ 75% agreement with the human majority on non-tie pairwise items** and **quadratic-weighted
κ ≥ 0.6 per absolute criterion**, against at least **40 labelled pairs and 20 labelled
documents** (at least 10 of each after a judge model or rubric change). The owner plus one other
person label for about 3 hours (BRD §1.7). Labels come only from dev runs; packs are never
exported from a held-out run (`calibration.allowed_case_sets`). Settings: `[calibration]` in
`config.toml`.

### Owner's workflow

```sh
# 1. a live dev run (or --cli for a dry run), judged: B0-B2 and the prototypes
uv run --project evals python -m evals all --live --generators b0-oneshot b1-parallel b2-chain p1-frame p2-brief p3-flow p4-editor

# 2. export one blinded pack (40 pairs + 20 documents by default; seeded)
uv run --project evals python -m evals label export --run-id <run_id>
#    -> evals/runs/<run_id>/calibration/packs/<pack_id>/{pack.html, pack.json, pack.md, labels.csv}

# 3. each labeller opens pack.html in a browser (offline, one file), enters a labeller ID,
#    labels every item and clicks "Download labels JSON". Progress is kept in the browser;
#    "Load labels…" resumes from a saved file. Without a browser: read pack.md, fill labels.csv.

# 4. import each labeller's file (one file per labeller; the IDs must differ)
uv run --project evals python -m evals label import labels-<pack_id>-owner.json
uv run --project evals python -m evals label import labels-<pack_id>-alex.json
uv run --project evals python -m evals label import labels.csv --labeller owner   # CSV fallback

# 5. compute agreement -> runs/<id>/calibration.md and .json, status into the manifest
uv run --project evals python -m evals calibrate --run-id <run_id>
uv run --project evals python -m evals report --run-id <run_id>   # adds "Judge calibration" to the report
```

Both labellers label the **same pack**: export once and hand the same folder to both. Give
labellers only the pack folder. The key that maps items back to generators stays in
`runs/<id>/calibration/keys/` and must not be shared.

### The pack (blinding)

- **Sampling.** Pairs come from the run's configured judge pairs (`judge.pairs` with both
  generators in the run), an equal share per generator pair; documents an equal share per
  generator (`n0-null` excluded). Within each share, picks spread over cases and families as
  evenly as the run allows. Sampling is seeded (`calibration.seed`, `--seed`): the same run,
  seed and sizes give the same pack (`pk-<hash>`). Pairs need no judge verdicts yet, so humans
  can label before the judge runs.
- **What labellers see** is what the judges see: the case brief, the outline with constraints,
  the data and attachments, and the anonymised Markdown (`normalised_markdown`). No generator
  names, case IDs, versions or judge output. Item IDs are random, items are shuffled (pairs
  first, then documents), and each pair's A/B sides are drawn at random. The questions and
  anchors are quoted from the judge rubrics. The page shows the Markdown rendered or raw; links
  are shown as text, never clickable.

### Importing labels

`label import` finds the run from the file's pack ID. It rejects the file, naming every problem,
for: a pack not exported from this run (wrong pack), unknown or repeated item IDs, a kind
mismatch, verdicts other than `A`/`B`/`tie`, scores outside the integers 1–5, missing or
half-filled items (unless `--allow-missing`), and a labeller ID that already imported into this
pack (unless `--replace`). Labels are stored per labeller in `runs/<id>/calibration/labels.jsonl`,
mapped to the configured pair's sides (`x`/`y`/`tie`) so they compare directly with the judge.

### Statistics

Every interval is a 95% cluster-bootstrap CI over cases (`calibration.bootstrap_resamples`).

| | Judge vs human | Human vs human |
|---|---|---|
| Pairwise, per question | **agreement with the human majority on non-tie items** (gated for `calibration.gated_questions`, default the overall verdict, which the win-rate gates use); 3-way raw agreement and Cohen's κ (A / B / tie); judge ties on non-tie items; agreement with each labeller | 3-way raw agreement, Cohen's κ, agreement where both chose a side, for every pair of labellers |
| Absolute, per criterion | **quadratic-weighted κ**, pooled over labellers (one point per document and labeller); exact and within-1 agreement; mean difference judge − human; κ per labeller | weighted κ, exact, within-1 |

- The judge's pairwise verdict is its both-orders verdict (a win only when both orders agree).
  A non-tie item is one where the human majority chose a side; a judge tie there counts as a
  disagreement. The human majority is the verdict more than half of the labellers gave, so two
  labellers who disagree make the item a human tie.
- Status: **PASS** when the label counts are met and every gated question reaches 0.75;
  **FAIL** when a gated question is below it; **NOT ENOUGH LABELS** below the counts. A
  criterion with κ < 0.6 does not fail calibration: it is reported as *not trusted* (not gated),
  as §6.3 says, and gates should not rely on it. Questions that are not gated are reported the
  same way.
- `--recalibration` (judge model or rubric changed) lowers the counts to
  `calibration.recalibration_min_labels` (10) of each kind. A calibration whose judge or
  absolute-rubric version no longer matches the run's is marked **stale** in the report.

### Single-labeller mode

The second labeller is not named yet, so the owner may label alone. Everything judge-vs-human is
still computed (the "human majority" is then the one labeller) and the thresholds are applied,
but the result is marked **provisional (single labeller)** and the human-vs-human tables say
"not computable". There is no estimate of how far two people agree, so a judge that misses 75%
may be no worse than a second human would be. Import the second labeller's file into the same
pack later and rerun `calibrate`; `--labellers owner` reproduces the single-labeller figures.
Results from FAKE or `--cli` runs are also marked provisional and never count toward EVAL-13.
