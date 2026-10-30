# 0004: API spike — findings and recommended BRD changes

**Status:** R1–R12 accepted and R13 deferred by the owner, 2026-10-30 (see Decision). The spike builder wrote the builder findings (WP-002) on 2026-10-30. The owner fills in the user sessions and the live call. **The owner must approve, reject or modify each recommended BRD change below before WP-003 (contract freeze) starts** (DEVELOPMENT_PLAN §0.2 rule 1, §0.4; BRD §10.5 lists §4.1–4.4, §4.13, §4.14 and §4.17 as API-sensitive).
**Requirements:** WP-002; BRD §1.5 (time to first document), §2 (UC-1, UC-2), §4.1–4.6, §4.13–4.14, §4.17 (frame), FR-PRV-4, §7
**Spike:** branch `spike/api` (never merged), code under `spike/`. How to run it and the full list of shortcuts are in `spike/README.md`.

## Problem

WP-003 freezes every public signature. After that, a change to the authoring API is an owner-escalated Contract Request. So the API has to be tried, by its author and by three people new to it, while it is still cheap to change.

## What was built

A throwaway prototype, about 1,900 lines of Python, that runs these end to end under `MockProvider`:

- **UC-1** (`spike/examples/01_weekly_status.py`): three independent sections, then a fan-in.
- **The BRD §7 example**, copied verbatim (`spike/examples/02_quarterly_report.py`, with the shipped `.mock.json`). It covers a subclass, `ctx`, a `Value` metric, a table, a helper outline, checks, a conditional section and the `by-theme` expansion.
- A quickstart (`spike/examples/00_quickstart.py`).

All three produce Markdown, HTML and JSON. Execution uses a ready queue under a semaphore, and a test shows that a slow prompt does not hold up unrelated downstream work (FR-EXE-3). Lazy references follow FR-REF-1/8/9 exactly: tokens, `ctx`, token mode versus placeholder mode, and misuse detection. The frame (FR-COH-1) is implemented, because it changes what every request looks like. Not implemented: cache, lock, budget, retries, events, `on_error="continue"`, brief, editor, flow, repetition detection and PDF. Checks only warn. The complete list is in `spike/README.md`. There are 14 smoke tests: `cd spike && uv run --group dev pytest -q`.

Two deliberate deviations from the BRD, each explained by a finding below:

1. Section IDs get their own namespace (F1).
2. A `Prompt` or `Value` passed in `context=` is rejected (F6).

## Builder findings (from writing and running the examples)

These come from the builder's own use, so they are biased: the builder knew the BRD before writing a line. The user sessions are the real test. Each finding has a confusion code (C-number) in `spike/user-test/SESSION_SCRIPT.md`, so the sessions can confirm or refute it.

### F1. The §7 reference example breaks its own ID rule (FR-DAG-1, FR-DOC-5, FR-PRM-5, FR-DYN-1) — **must fix before freeze**

Under FR-DAG-1, sections, prompts, values and expansions share one ID namespace, and an explicit duplicate raises `DuplicateIdError`. In §7, the section "Risks" gets the slug `risks`, and its prompt is `Prompt(..., id="risks")`. "Recommendations" and `id="recommendations"` collide the same way. With the BRD rule, the reference example raises `DuplicateIdError: ID 'risks' is already used` before it runs. The test `test_brd7_example_collides_under_brd_shared_namespace` shows this. §7 already works around the same rule once ("the section 'Themes' has the ID `themes`, so the expansion uses `by-theme`"). Naming a prompt after its section is the most natural thing an author does, so users will hit this constantly (C13).

Options:
- (a) Separate namespaces: section IDs in one, node IDs (prompts, values, expansions) in the other. Anything that takes "an ID", such as `--only`, `lock update --section/--prompt` or `content.find`, already knows which kind it wants.
- (b) Keep one namespace, but let generated section slugs yield: add `-section`, or a numeric suffix, when they collide with an explicit node ID. This depends on order, because the section is usually created first.
- (c) Keep the rule, rewrite §7 with `id="risks-assessment"` and so on, and document it.

**Recommendation: (a).** The spike runs with (a). Expansion IDs then default naturally to the containing section's ID (see F5c).

### F2. Implicit registration is the nicest part of the API — with one sharp edge (FR-PRM-2, FR-PRM-3, FR-PRM-10)

`with doc.section(...)`, followed by plain `Prompt(...)` calls, reads well, and UC-1 is 15 lines. The sharp edge is that whether a prompt appears in the output depends on *where it was constructed*:

- A prompt built one line above the `with` block is silently a helper that never runs, unless something references it.
- §7's `outline` is a helper twice over: it is created outside a section *and* has `visible=False`. A reader can't tell which of the two matters (C6, C12).

The spike implements the FR-PRM-10 `OrphanPromptWarning`, and it fires correctly. Recommendation:
- Keep the design and keep FR-PRM-10 a MUST.
- In docs and examples, say "helper = `visible=False`" as the single way to make a helper.
- Drop the redundant placement in §7: move `outline` inside the Themes section with `visible=False`, or leave it outside without the flag.

No signature change.

### F3. f-string token pitfalls (FR-REF-1, FR-REF-8, FR-REF-9, FR-ERR-2)

What happened when each likely slip was tried:

| Slip | Spike behaviour (= BRD) | Verdict |
| --- | --- | --- |
| `print(summary)` | prints `⟦doccode:ref:7c33b3987ea9⟧` | Confusing but harmless (C1). Docs. |
| `"Based on " + summary` | `TypeError: can only concatenate str (not "Prompt") to str` | Python's message never mentions DocCode (C3). |
| forgot the `f`: `"Based on {summary}"` | `UnknownPlaceholderError` with the hint "use an f-string or `refs=`" | Good, **if** the hint is required. |
| `f"{summary!r}"`, `.upper()`, slicing | `TokenMisuseError` naming the prompt | Good. |
| `f"For {ctx.company} … {issues}"`, meaning `{issues}` as a placeholder | **sent literally, silently** (token mode) | The one silent failure (C10). |

Recommendations:
- **FR-REF-8, add a MUST:** at validation, a token-mode text that contains `{identifier}`, where the identifier is a key of the prompt's resolved context or of `refs=`, raises `UnknownPlaceholderError` (or warns). The message should say: "this text is in token mode, so `{issues}` is sent literally; write `{ctx.issues}`". This closes the only silent path the spike found.
- **FR-ERR-2 / FR-REF-8:** require the `UnknownPlaceholderError` hint to suggest an f-string when the placeholder name matches nothing. The spike's message is a workable starting point.
- **FR-REF-1, add a SHOULD:** `Prompt`, `Value` and the `ctx` reference implement `__add__`/`__radd__` with `str`, returning the token-bearing string. It costs little and removes C3.

### F4. Format specs on a `Value` are natural and forbidden (FR-REF-9, FR-VAL-1, FR-REF-1 grammar) — **decide before freeze**

`growth = Value(lambda: 0.25)` followed by `f"growth was {growth:.0%}"` is what a data analyst will type (C4). FR-REF-9 makes any format spec a `TypeError`, so §7 formats inside the lambda and returns the string `"25%"`. That throws away the number, which `numbers_from` and tables would rather keep.

Recommendation: extend the FR-REF-1 grammar, which is fixed at the freeze, with `⟦doccode:ref:<key>|fmt=<spec>⟧`. It is allowed on `Value` references only, and applied as `format(result, spec)` at substitution. Format specs on `Prompt` references keep raising, because the output is text.

If the owner declines, the `TypeError` message must say "format inside the Value function". The spike's message already does.

### F5. Expansion ergonomics (FR-DYN-1, FR-DYN-2, FR-DYN-5, FR-CTX-4)

The expansion worked first time and reads well: a nested function plus `parse.list_items` is a familiar shape. Four problems:

- **(a) §7 uses the non-primary style inside the expansion.** It writes `Prompt("Explain the theme {theme} for {company}…", context={"theme": theme})` in placeholder mode, while §4.4 says `ctx` is "the primary documented style". The `ctx` form also works, and it can mix in references, which placeholder mode cannot: `Prompt(f"Explain the theme {ctx.theme} for {ctx.company}, using {outline}.", context={"theme": theme})`. **Recommendation:** use this form in §7 and in the FR-DYN-2 docs (C8).
- **(b) Model text can silently fill placeholders.** In a probe, the build function wrote `Prompt(f"Explain {item}")`. The model's item was `Use {company} pricing`, and the document context has `company`. The instruction sent was `Explain Use Acme pricing`. When no context name matches, the spike raises with the FR-DYN-2 hint, but a match is silent. FR-DYN-2 relies on documentation alone. **Recommendation, new FR-DYN-7 MUST:** while an expansion build runs, a placeholder-mode instruction whose placeholder text `{name}` also appears verbatim in any of the build's input results raises `UnknownPlaceholderError`, with the "pass model text through `context=`" hint. This is cheap and exact, because the executor holds the build's inputs.
- **(c) The default expansion ID is unspecified.** `id=None` in FR-DYN-1 has no defined default, and the spike invented `_expand.<n>`. With F1(a), **recommend** the default be the containing section's ID, so §7 needs no `id=` and its subsections become `themes/pricing-pressure`. For an expansion at document level, recommend `_expand.<n>`.
- **(d) Generated IDs depend on model output.** `by-theme/pricing-pressure` is part of mock-map keys, `--only` and lock entries, and it is stable only while the outline is. FR-DYN-5 already says this. No change.

### F6. A prompt passed through `context=` creates no edge (FR-CTX-2, FR-REF-2, FR-REF-5)

`Prompt("Write about {thing}", context={"thing": other_prompt})` looks right, and authors will write it (C11). Under FR-CTX-2 the value is `str()`-ed, which gives the raw token. Context is inserted literally (FR-REF-3), so the model receives `⟦doccode:ref:…⟧`. No edge is added, and FR-REF-9 never sees the token, because context values are not scanned. A probe of the pre-fix spike ran the dependent with a `Value` that had never run, and sent "Length None".

**Recommendation, FR-CTX-2:** a `Prompt`, `Value` or `ctx` reference found as a context value at any level raises `TokenMisuseError` at validation, with the hint "use `refs=` or an f-string reference". The alternative, silently treating it as `refs=`, would blur the two namespaces that FR-REF-5 keeps apart. The spike raises.

### F7. Generated IDs are what mock maps need, and they are awkward (FR-PRM-5, FR-PRV-4)

In the quickstart, a one-prompt section's prompt is `highlights.1`. Authors only learn this from `plan()`, and they need it for the first mock map they write (C7). The unknown-key `ConfigError` with "did you mean" worked well; FR-PRV-4 is right to require it.

**Recommendation, FR-DAG-3 / FR-DAG-4, add a SHOULD:** `plan.mock_template()` returns a JSON skeleton of every prompt ID, ready to paste into `<script>.mock.json`. Docs keep saying "use `id=` for anything a mock map, lock or `--only` names". No signature change to `Prompt`.

### F8. The mock's default output cannot drive an expansion (FR-PRV-4, FR-CLI-3)

With no mock map, the outline's default answer is `[mock:outline] List the 3 themes…`. `parse.list_items` raises, so the run fails with `ExecutionError` from `by-theme`. A new user dry-running anything with an expansion meets this first (C9). The quickstart's own "more things" snippet fails without a map.

**Recommendation, FR-PRV-4:** for a prompt that is in some expansion's `after`, the default mock output is a three-item bulleted list (`- <prompt-id> item 1` …). The executor already knows this and can pass `feeds_expansion: true` in `request.metadata`. Keep shipping `.mock.json` files for realistic structure.

### F9. Mock output nests and truncates (FR-PRV-4)

The default output echoes the first 80 characters of the *resolved* instruction. Downstream outputs therefore read `[mock:analysis] Using [mock:summary] Write a 2-paragraph summary of Acme Inc's Q3 2026, built ar`. The cut-off usually falls before the part that would show the wiring.

**Recommendation:** the default output becomes `[mock:<id>] <first 80 chars of the unresolved instruction, tokens shown as {id}>`. The dry run then shows the graph: `[mock:analysis] Using {summary} and the attached data…`. This is a small FR-PRV-4 wording change.

### F10. The content model cannot keep FR-DOC-7 order (FR-CNT-2, FR-DOC-7)

`SectionContent` has `blocks` and then `subsections`. Text or a prompt added to a section *after* a nested subsection is opened renders before that subsection. That contradicts FR-DOC-7's "blocks within a section keep insertion order". Options:
- (a) One ordered `items: tuple[Block | SectionContent, ...]`, with `blocks` and `subsections` as derived properties.
- (b) Raise when a block is added to a section after one of its subsections was opened.

**Recommendation: (a).** It is a content-model field change, so it must land at the freeze (FR-CNT-1/2, and FR-CNT-7 schema).

### F11. Ambiguities to settle in the BRD (no strong preference; the spike's choice is in brackets)

1. **Default `Value` ID.** FR-PRM-5 covers prompts only. [`_anon.<n>`, shared with helper prompts]
2. **Does an unreferenced `Value` run?** FR-DAG-1 says "registered prompts, values", but values are never registered anywhere. [Values join the graph only when reached.]
3. **The frame's expansion line `<title> — subsections generated at run time`.** Whose title? [The containing section's title]
4. **Position-line numbering for nested and expanded sections.** FR-COH-1's example says "section 4". [Dotted: `section 2.3 "Hiring"`; expansion children continue the parent's numbering.]
5. **ID of a prompt created directly inside an expansion, outside any section.** [`<expansion-id>.<n>`]
6. **§7's "runs unchanged" stages.** The file's `__main__` calls `execute(lock="doccode.lock")` and `save("q3.pdf")`, so as a script it needs lock and PDF support, plus a provider from somewhere other than the file itself. Recommend the Phase 2 stage say explicitly "via `build()` only". The spike runs it that way.
7. **Section IDs containing `/`** (FR-DYN-5) end up as HTML `id` and anchor values. These are valid HTML5 but awkward in CSS selectors. Recommend the HTML renderer escape them in anchors. No contract change.

### F12. What worked and should not change

- `ctx` in f-strings. Every value is one brace style, and nothing in UC-1 or §7 needed `{{ }}`.
- The ready-queue executor. It is simple: about 40 lines with `asyncio.wait(FIRST_COMPLETED)`.
- `plan()` as a first-class "show me before you spend". `Value` plus `metric()` for figures.
- Single-pass resolution: model output containing `{data}`, `{{` or a token passes through untouched (tested).
- Definition order makes cycles hard to write by accident in plain authoring code; they come mostly from `depends_on` across expansions and check sources. `CycleError` stays, but it will be rare.
- The worker-thread `execute()` inside a running loop works, so notebooks need no `await`.
- On Python 3.14, which `uv` picked here, t-strings (FR-REF-6) are available. The token design does not block adding them later.

## Recommended BRD changes (for owner decision)

| # | BRD IDs | Change | Freeze-blocking? | Owner decision |
| --- | --- | --- | --- | --- |
| R1 | FR-DAG-1, FR-DOC-5, FR-PRM-5, FR-DYN-1, §7 | Section IDs and node IDs in separate namespaces (F1a); §7 then runs as written | **Yes** | |
| R2 | FR-DYN-1, FR-DYN-5, §7 | The default expansion ID is the containing section's ID (`_expand.<n>` at document level); drop `id="by-theme"` from §7 (F5c) | **Yes** | |
| R3 | FR-REF-1, FR-REF-9, FR-VAL-1 | Add the `\|fmt=<spec>` token form for `Value` references, so `f"{growth:.0%}"` works (F4) | **Yes** (grammar is frozen) | |
| R4 | FR-CNT-1, FR-CNT-2, FR-CNT-7, FR-DOC-7 | `SectionContent.items` keeps blocks and subsections in insertion order (F10a) | **Yes** | |
| R5 | FR-CTX-2 | A `Prompt`/`Value`/`ctx` reference in `context=` raises `TokenMisuseError` (F6) | **Yes** (behaviour of a MUST) | |
| R6 | FR-REF-8 | Token-mode text with `{name}` matching a context or `refs` name raises, with a "use `{ctx.name}`" hint (F3) | No (validation only), but cheap to settle now | |
| R7 | FR-DYN-2 (new FR-DYN-7) | During an expansion build, a placeholder whose `{name}` text appears in the build's inputs raises (F5b) | No | |
| R8 | §7, FR-DYN-2 docs | Use `ctx` style inside the expansion: `f"… {ctx.theme} for {ctx.company}, using {outline}"` (F5a); make `outline` a helper in one way only (F2) | No (example text) | |
| R9 | FR-PRV-4 | The default mock output for a prompt feeding an expansion is a 3-item list; default output shows unresolved references as `{id}` (F8, F9) | No | |
| R10 | FR-REF-1 | `__add__`/`__radd__` with `str` on references (F3) | No (SHOULD) | |
| R11 | FR-DAG-3/4 | `plan.mock_template()` (F7) | No (SHOULD) | |
| R12 | FR-ERR-2, FR-REF-8 | Require the "did you mean an f-string?" hint on `UnknownPlaceholderError` when the name matches nothing (F3) | No | |
| R13 | FR-PRM-5, FR-DAG-1, FR-COH-1, §7 | Settle the F11 ambiguities 1–6 | Items 1, 2 and 5 are signature-adjacent; settle them before freeze | |

The user sessions may add to this list, remove from it or re-prioritise it. A change the sessions contradict should be dropped.

## User sessions (owner fills in)

Run per `spike/user-test/SESSION_SCRIPT.md`. One `RESULTS_TEMPLATE.md` copy per tester. Summarise here.

### Session 1: P1 Python developer / AI engineer

- Date, tester background:
- **Time to first document:** ____ (target ≤ 10:00, BRD §1.5)
- Tasks 2–4 (time; completed / assisted / failed):
- Confusion codes seen (C1–C15, other):
- Debrief answers 1–2 (mental model correct?):
- Key quotes:

### Session 2: P2 Data scientist / analyst

- Date, tester background:
- **Time to first document:** ____
- Tasks 2–4:
- Confusion codes seen:
- Notebook experience:
- Key quotes:

### Session 3: P3 DevOps / CI owner

- Date, tester background:
- **Time to first document:** ____
- Tasks 2–4 (incl. which error messages in Task 4 were actionable):
- Confusion codes seen:
- Trust in `--dry-run` for CI:
- Key quotes:

### Across sessions

- Median time to first document:
- Findings confirmed (F-numbers):
- Findings not seen:
- New problems not predicted:

## Owner's live call (owner fills in)

Run per `spike/README.md` → "Owner's live call". The `doccode.live` adapter is untested by the builder.

- Date, model, effort:
- Did it run first time? Errors:
- Did sections stay in scope (frame and position line)? Repetition between sections?
- Clean-up needed (preamble, chatter, headings)? Did FR-OUT-2/3 handle it?
- Tokens and cost reported by the API:
- Anything that changes a recommendation above:

## Decision (owner, 2026-10-30)

- **Recommendations accepted:** R1–R12, all as recommended above. The BRD carries each one, marked "(amended 2026-10-30, decision 0004 R<n>)":
  - R1: FR-DAG-1, FR-DOC-5, FR-PRM-5, FR-DYN-1, FR-DYN-5; FR-LCK-2 (`only=` takes both kinds of ID) for consistency
  - R2: FR-DYN-1, FR-DYN-5; `id="by-theme"` dropped from §7
  - R3: FR-REF-1 (`|fmt=<spec>` token), FR-REF-9, FR-VAL-1
  - R4: FR-CNT-1, FR-CNT-2, FR-CNT-7, FR-DOC-7; FR-RND-3 (Markdown renders `items` in order) for consistency
  - R5: FR-CTX-2
  - R6: FR-REF-8 (raises; no warn-only variant)
  - R7: new FR-DYN-7, pointed to from FR-DYN-2
  - R8: §7 and FR-DYN-2 (`ctx` style in the expansion; `outline` is a helper by `visible=False` only, created inside the Executive Summary section because the summary's f-string needs it first)
  - R9: FR-PRV-4; FR-PRV-1 (`feeds_expansion` in `request.metadata`)
  - R10: FR-REF-1 (SHOULD)
  - R11: FR-DAG-3, FR-DAG-4 (SHOULD)
  - R12: FR-REF-8, FR-ERR-2
- **Deferred:** R13 (the F11 ambiguities 1–6) until after the user sessions. It is an open question in BRD §8, marked "decide before WP-003". F11 item 7 (escaping `/` in HTML anchors) needs no contract change and is left to the HTML renderer.
- **Rejected:** none.
- **Clarifications added while amending** (details the recommendations left open; for owner review):
  - A default expansion ID is assigned when the graph is built or extended, after explicit IDs are known, and takes a numeric suffix (`-2`) when already taken, for example by a second expansion in the same section (FR-DYN-1).
  - An `only=` entry that names both a section and a prompt selects both (FR-LCK-2).
  - `plan.mock_template()` returns JSON text mapping every prompt ID known before the run to its default mock output, leaving out `_brief`, `_editor` and expansion-created prompts (FR-DAG-4).
  - If `format(result, spec)` raises for a `fmt` token, the substitution fails like a failed node (FR-VAL-1); a `fmt` token on a prompt key is a `TokenMisuseError` (FR-REF-1).
- **User sessions:** session 1 is run by the owner (P1 developer persona). Sessions 2 (P2 data analyst) and 3 (P3 DevOps/CI) are simulated by fresh agents with only the session kit, the method WP-505 uses (owner decision, 2026-10-30). Only session 1 gives a human timing. The session results may still change the recommendations above: a change the sessions contradict goes back to the owner.
- BRD amended on branch `docs/brd-amendments-2026-10-30`, 2026-10-30, in the commit that fills in this section. WP-003 may start: **not yet**. R13 must be settled after the user sessions, and G0 (WP-020, decision 0006) must pass first.

## Consequences

- WP-003 freezes the API from the BRD *as amended after this record*, not as of today.
- The spike branch stays unmerged. Nothing in `spike/` is reused as implementation. The smoke tests are ideas for WP-1T at most.
- R1, R2 and R8 change the §7 example text, which is shared by the eval adapter (`evals/generators/doccode_adapter/`) and by the Phase 2 test. Both are updated after the BRD amendment.
