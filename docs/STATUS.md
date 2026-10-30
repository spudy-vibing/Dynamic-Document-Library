# DocCode Status

The single place to see what is done and what is left, across sessions. Read this first at the start of every session; update it at the end.

**Last updated:** 2026-10-30 · **Current stage:** Wave 0 (eval MVP code built and committed; owner decisions made; awaiting the owner's first live run)

## How to use this file

- `[x]` done · `[~]` in progress or partly done · `[ ]` not started · `[!]` blocked (say on what)
- When you tick an item, add the date and evidence: test counts, a run ID, a file path or a decision record. Gate items need the owner's sign-off, recorded as "signed off by owner, date".
- Work-package IDs and their full scope are in `DEVELOPMENT_PLAN.md` §2. Requirement IDs are in the BRD. This file tracks progress only; it does not redefine scope.
- Add one line to the session log at the bottom for every working session.

## Next up

1. **Owner:** review the frozen baseline prompts (`evals/generators/prompts/*.md`) and the judge rubric (`evals/judge/rubrics/pairwise.md`).
2. **Owner:** check the prices in `evals/config.toml`, then make the first live eval run (about $7 expected, $15 cap):
   `ANTHROPIC_API_KEY=... uv run --project evals python -m evals project`, then `... all --live`.
3. **Owner:** name the second person for judge calibration labelling (BRD §1.7, EVAL-13), or ask to switch to owner-only labelling.
4. **Next build work:** the rest of Wave 0 evals (WP-015..WP-019) and the library-side Wave 0 (WP-000, WP-001, WP-002).

## Open decisions (owner)

All resolved on 2026-10-30 (owner accepted every recommendation).

- [x] Refusals: new **FR-PRV-8** — `ProviderRefusalError` with the category; `ErrorBlock` under `on_error="continue"`; no retry, no cache or lock write, never re-routed to another model. Added to WP-300, HLD §4.3, eval proposal. 2026-10-30
- [x] Input-injected URLs stay clickable: accepted for 0.1.0 as a documented limitation (NFR-26); strict mode `link_allowlist_only=True` on the 0.2.0 roadmap (BRD §11). 2026-10-30
- [x] No LangGraph as the engine: `docs/decisions/0008-orchestration-engine.md`; integration adapter on the roadmap ("Later"). 2026-10-30
- [x] `git init` locally; `docs/` and `evals/` committed (offline fake-mode scoreboard summaries git-ignored). GitHub repo still to be created by the owner (G0 item). 2026-10-30
- [x] Plan §0.6 standing decisions, BRD §1.5 targets and §1.7 assumptions confirmed; BRD §8 questions resolved except package name, PDF engine (WP-000) and the post-0.1.0 second provider. Open item: the second labeller is not yet named. 2026-10-30
- [x] Per-wave spend caps set (table below). 2026-10-30

## Planning documents

- [x] BRD written and revised: requirements FR/NFR plus EVAL-1..24, full 0.1.0 scope (`docs/DocCode BRD Write Documents Like Code.md`), 2026-10-29
- [x] Development plan v1.5, eval-first (`docs/DEVELOPMENT_PLAN.md`), 2026-10-29
- [x] Eval proposal aligned with the BRD (`docs/EVALS_PROPOSAL.md`), 2026-10-29
- [x] High-level design (`docs/HLD.md`), 2026-10-29
- [x] Readiness review: blockers and contradictions fixed; `temperature` replaced by `effort`, 2026-10-29
- [x] Consistency check: all 169 requirement IDs (170 with FR-PRV-8, added 2026-10-30) referenced by the plan, no dangling IDs, mermaid matches Depends, 2026-10-29

## Wave 0: prior art, scaffold, evals and baselines, spike, contract freeze

**Eval MVP**
- [x] WP-010 Harness core: `LLM` wrapper (explicit effort, no sampling params, no fallbacks, served-model check, refusals as error rows), storage, manifest, resume, cost projection, CLI. 2026-10-29
- [x] WP-011 Fixtures and the 6 MVP cases (3 quarterly, 2 weekly, 1 README) with traps and `truth.json`; regeneration test. 2026-10-29
- [x] WP-012 Graders (numbers / silent UNR / traps, 6-gram repetition, structure, chatter, adherence, data blocks, cost), perturbation suite, null baseline, import check. 2026-10-29
- [x] WP-013 Baselines B0 one-shot, B1 naive parallel, B2 chat chain, with frozen versioned prompts. 2026-10-29
- [~] WP-014 Pairwise judge (both orders, Batches API), `report`, `compare`. Code done 2026-10-29. Left: the owner's first live run and labelling 20 pairs.
  - Evidence: `uv run --project evals pytest` → 226 passed, 2 skipped; offline `python -m evals all` → 54 docs, 819 score rows, 72 judge calls; projection $6.70 expected, $40.17 worst case, $15 cap.

**Rest of Wave 0 evals**
- [ ] WP-015 Absolute rubric (six criteria) and claim checker
- [ ] WP-016 Full dev set (24 cases, F1–F7) and held-out generators with owner-held seeds
- [ ] WP-017 Coherence prototypes P1–P4 and the ablation report
- [ ] WP-018 Calibration tooling (labelling flow, agreement statistics)
- [ ] WP-019 Eval CI (offline job on every PR, owner-approved live workflow)
- [ ] WP-020 G0 eval run (owner): baselines and prototypes on the dev set, 3 reps; calibration; decision record 0006 (default presets, tuned prompts)

**Library side**
- [ ] WP-000 Prior art, PyPI name, PDF engine and model IDs (decision records 0001–0005)
- [ ] WP-001 Repo scaffold (pyproject, CI matrix, ownership and traceability scripts)
- [ ] WP-002 API spike and three-person user test (decision record 0004)
- [ ] WP-003 Contract freeze (after WP-020 and owner approval of spike-driven BRD changes)

**Gate G0**
- [ ] Eval MVP live run done; baseline scoreboard published
- [ ] Judge calibrated; prototype ablation recorded (0006)
- [ ] Decision records 0001–0006 merged; spike findings approved into the BRD
- [ ] `make check` green on the stub repo; `import doccode` exports only the approved names
- [ ] Owner sign-off

## Wave 1: authoring API, values, clean-up, content model (offline)

- [ ] WP-100 Context scopes and `ctx` references
- [ ] WP-101 `Prompt`, tokens, misuse detection, orphan registry
- [ ] WP-102 `Document`, `Section`, expansion registration, data blocks, attachments
- [ ] WP-103 Graph, validation, plan, coherence nodes
- [ ] WP-104 Content model, `link_hosts`, provenance stripping
- [ ] WP-105 `MockProvider`
- [ ] WP-106 Renderer registry, Markdown and JSON renderers
- [ ] WP-107 Minimal sequential executor
- [ ] WP-108 UC-1 example end to end under mock
- [ ] WP-109 `Value` and `parse` helpers
- [ ] WP-110 Output clean-up (`hygiene.py`)
- [ ] WP-111 Outline frame and position line
- [ ] WP-112 Minimal run records
- [ ] WP-1T Test-author lane (adversarial suites, property tests)
- [ ] WP-113 Eval adapter, `authored` mode under mock
- [ ] Gate G1 criteria met and owner sign-off

## Wave 2: execution engine, expansions, checks, lockfile (offline)

- [ ] WP-200 Cache
- [ ] WP-201 Retry and timeout
- [ ] WP-202 Run records and events
- [ ] WP-203 Config chain
- [ ] WP-204 Mock extensions
- [ ] WP-205 Checks
- [ ] WP-206 Rate limits
- [ ] WP-207 Lockfile
- [ ] WP-208a Executor: scheduler and failure semantics
- [ ] WP-208b Executor: expansion runtime
- [ ] WP-208c Executor: checks, lock modes, rate limits
- [ ] WP-208d Executor: coherence runtime
- [ ] WP-209 Benchmarks
- [ ] WP-210 Coherence functions (brief, editor edits, repetition)
- [ ] WP-211 BRD §7 example under mock
- [ ] WP-2T Test-author lane
- [ ] WP-212 Eval adapter, `auto` mode and presets
- [ ] Gate G2 criteria met and owner sign-off

## Wave 3: Anthropic provider, prompt caching, cost, budget, first live DocCode eval

- [ ] WP-300 `AnthropicProvider` (effort, prompt caching, no sampling params)
- [ ] WP-301 Pricing and cost
- [ ] WP-302 Budget
- [ ] WP-303 Structured output (may be cut whole at G3)
- [ ] WP-304 Live test (owner)
- [ ] WP-305 First live DocCode eval (owner; advisory); decision record 0007 (presets)
- [ ] Gate G3 criteria met and owner sign-off

## Wave 4: HTML, PDF, templates, examples

- [ ] WP-400 HTML renderer and link policy
- [ ] WP-401 Template loader
- [ ] WP-402 PDF renderer (restricted URL fetcher)
- [ ] WP-403a `default` template
- [ ] WP-403b `corporate`, `minimal`, template guide
- [ ] WP-404 Content helpers for templates
- [ ] WP-405 JSON Schema
- [ ] WP-406 `context.file()` / `files()`
- [ ] WP-407 Reference examples 3–6
- [ ] WP-408 Template CLI
- [ ] WP-409 Golden tests and PDF smoke
- [ ] Gate G4 criteria met and owner sign-off

## Wave 5: CLI, diff, docs, release

- [ ] WP-500 Full CLI
- [ ] WP-501 Ref truncation and PEP 750 t-strings
- [ ] WP-502 Content diff
- [ ] WP-503 Docs site, README, policies
- [ ] WP-504 Release 0.1.0 (owner publishes)
- [ ] WP-505 DX agent proxy
- [ ] WP-506 Held-out release eval (owner; blocking)
- [ ] Gate G5 criteria met and owner sign-off

## Spend

Caps are set by the owner before each wave is dispatched (plan §0.8). Record actual spend after each live run.

| Wave | Cap (USD) | Spent so far | Runs |
| --- | --- | --- | --- |
| 0 | $200 (set 2026-10-30) | $0 | none yet |
| 1 | $25 | $0 | — |
| 2 | $25 | $0 | — |
| 3 | $250 | $0 | — |
| 4 | $25 | $0 | — |
| 5 | $150 | $0 | — |
| **Total** | **$675** | $0 | |

Caps cover Anthropic API spend on the owner's key and include the plan's 20% contingency; agent time is not counted. Dispatch pauses at 80% of a wave's cap.

## Live eval runs

| Run ID | Date | Mode | Generators | Cost | Result / link |
| --- | --- | --- | --- | --- | --- |
| — | — | — | — | — | No live run yet |

## Known notes and deviations

- `evals/tests/__init__.py` was added so `uv run --project evals pytest` works from the project root (2026-10-29).
- Eval prices for cache writes in `evals/config.toml` are estimates (1.25× input); verify before the first live run.
- Reps of the same case run within 5 minutes can share a provider prompt cache, which affects cost and cache-hit numbers but not text.
- Eval code: 59 Python files, about 7,600 lines, under `evals/`. Run outputs (`evals/runs/`) are git-ignored; only `evals/scoreboard/` summaries are meant to be committed.

## Session log

| Date | What happened |
| --- | --- |
| 2026-10-29 | Reviewed and expanded the BRD; wrote the development plan (v1.0 → v1.5); red-team review; coherence layers; readiness review and fixes; eval proposal; HLD; built the eval MVP (WP-010..WP-014 code) and verified it offline. |
| 2026-10-30 | Owner made all six open decisions (accepted every recommendation): FR-PRV-8 refusals; NFR-26 limitation accepted, strict mode on roadmap; decision record 0008 (no LangGraph); §0.6/§1.5/§1.7 confirmed; spend caps set; `git init` and first commit. |
