# WP-002 session results: tester P3 (DevOps / CI owner)

> **SIMULATED SESSION (agent playing P3): weaker evidence than a real user.**
> An AI agent (Claude) played the P3 persona from BRD §2 ("regenerate docs on a schedule or on merge, fail loudly, cap spend; touches CLI, exit codes, cache"). The times below are **estimates of a human P3's wall time**, not stopwatch readings. The agent reads faster than a human and has seen many Python CLIs, so treat the friction found as a lower bound and the times as optimistic. Use it to sharpen what to watch for in a real P3 session, not instead of one.

- Date: 2026-10-30
- Moderator: none (self-run by agent; session rules from `SESSION_SCRIPT.md`)
- Tester background (simulated): owns CI pipelines, competent Python reader, not an LLM user; has `uv`, uses a terminal + editor
- Environment: macOS (Darwin 25.6), zsh, `uv`, Python from `spike/.venv`, branch `spike/session-p3` off `spike/api` @ `fa7363c`
- Recording saved at: n/a. Files written: `release_notes.py`, `tests/test_weekly.py`, `break/*` (outputs in `out/` and `release-notes.*` are git-ignored by `spike/.gitignore`, so the verbatim outputs are quoted in the appendix instead)

**Deviations from the script.** (1) The script says the tester sees only QUICKSTART and TASKS; the agent was also allowed `spike/README.md` and `--help`, and read the README before starting. That leaks the "Shortcuts" table (no cache, lock, budget), so the "expected but missing" answers are less naive than a real P3's. (2) To keep every change inside `sessions/p3/`, `release_notes.py` was created there (not in `spike/`), the Task 3 test is `sessions/p3/tests/test_weekly.py` (not `spike/tests/`), and Task 4 copies live in `sessions/p3/break/`. `uv run` found the project from the subfolder, so this did not change the experience. (3) The library source under `spike/doccode/` was not read; only `help()`, `--help`, `__all__` and runtime `repr`s were used. Two raw tracebacks displayed paths in `doccode/__main__.py`, nothing more.

## Timings

Estimated wall time for a human P3, mm:ss.

| Task | Start | End | Duration | Completed? (yes / assisted / no) | Notes |
| --- | --- | --- | --- | --- | --- |
| 1. First document (QUICKSTART) | 0:00 | ~5:30 | **TTFD: ~5:30** | yes | 3 steps + "open them". Read ~2:00, `uv sync` ~0:30 (uv already installed), paste file ~1:00, run ~0:15, inspect `.md` and `.html` ~1:30. Hesitations: a `warning: Ignoring invalid SSL_CERT_FILE` line (environment, not DocCode, but it's the first thing printed); mock text cut mid-word (`...for Acme C`); "where did the files go?" (CWD). No errors. |
| 2. Run it like CI | 0:00 | ~3:00 | ~3:00 | yes | The example's docstring gives the exact command. Ran without `--dry-run` first on purpose → `config error`, **exit 3** (good). `--dry-run -o … -o …` created the missing `out/` dir. `plan` and `plan --mermaid` worked. Extra probing of exit codes and determinism: ~4 more min (not counted). |
| 3. A pytest test | 0:00 | ~9:00 | ~9:00 (budget 5) | yes, over time | Stuck points: the example file name starts with a digit, so `import` doesn't work → `importlib.util.spec_from_file_location` boilerplate (~2 min). `help(Document.execute)` shows only `execute(self, **opts)`; `RunRecord` has no docs. Found `content.run.prompts[i].started_at/ended_at` only by printing the whole record (~3 min). Unsure whether `content.find()` takes the section ID or the prompt ID (both are `next-week` here). 3 tests pass. |
| 4. Break it on purpose | 0:00 | ~6:00 | ~6:00 (budget 4) | yes | a: **silent wrong output with exit 0** (see C-new-1). b: clear error, exit 3. c: clear-enough error, exit 2. Extra time went on 4a: noticing the plan lost two edges and then checking what was sent. |

**Time to first document (BRD §1.5, target ≤ 10:00):** ~5:30 (estimated) → **met**. Why: QUICKSTART is copy-paste with no keys, no config and no choices; `uv sync` is fast once uv exists. A P3 without `uv` installed would add 2-5 min. The risk to the target is not the API but the environment (uv, Python ≥ 3.10).

## Confusions seen

| Code | Seen? | Time | What they said or did |
| --- | --- | --- | --- |
| C1 print shows a token | no | | Not exercised on this track. |
| C2 forgot the `f` | **yes, two forms** | T4a | Literal reading of "remove the `f` in front of the Next Week prompt string" removes only the first `f` of two implicitly concatenated f-strings. Result: no error, exit 0, `{shipped}` and `{wip}` sent literally (see C-new-1). Removing **both** `f`s gives the predicted error, exit 2: `validation error: next-week: unknown placeholder {shipped} in the instruction.` / `hint: if this is a Python variable holding a Prompt or Value, use an f-string (f"...{shipped}...") or refs={'shipped': shipped}`. "That one I can act on. And `plan` catches it, so it can be a cheap CI gate." |
| C3 `+` / `.format()` with a prompt | no | | |
| C4 format spec on a Value | no | | |
| C5 expected Prompt to run at once | no | | P3 thinks in build graphs ("it's a Makefile"), so lazy prompts felt natural. |
| C6 prompt outside the `with` block | no | | |
| C7 had to discover generated IDs | partly | T1, T4b | In T1 the plan's `highlights.1 [prompt, generated id]` was understood. The T4b error sends you to `doc.plan()` for IDs; fine. The example uses explicit `id=` so no guessing in T3. |
| C8 expansion: why `context=` | no | | Not on this track. |
| C9 expansion failed without a mock map | no | | Not on this track. |
| C10 `{name}` sent literally in an f-string | **yes (variant)** | T4a | The mixed plain/f-string concatenation is the same failure: literal `{shipped}` reaches the model silently. |
| C11 prompt passed in `context=` | no | | |
| C12 two kinds of helper prompt | no | | |
| C13 section/prompt ID clash | hesitation | T3 | "`content.find('next-week')`: is that the section or the prompt? They're both `next-week`." Worked, but only because the slug equals the prompt ID. |
| C14 no provider configured | seen, not confused | T2, T3 | Deliberately ran without `--dry-run`: `config error: No provider configured (the spike has no default live provider).` / `hint: run `python -m doccode run <file> --dry-run`, or doccode.configure(provider=MockProvider(...)); for the owner's live call, see spike/README.md (doccode.live.AnthropicProvider)`, **exit 3**. "Good: non-zero, distinct code, tells me the fix." |
| C15 unactionable error message (paste it) | **yes** | T2 probes | (1) `-o x.txt` → `error: cannot infer a format from 'x.txt'.` / `hint: pass format=`, exit 1. There is no `--format` flag on the CLI; the hint is for the Python API. (2) Missing script → raw `Traceback … FileNotFoundError: [Errno 2] No such file or directory: '…/examples/nope.py'`, exit 1. (3) `--entry nothere` → raw `Traceback … AttributeError: module 'release_notes' has no attribute 'nothere'`, exit 1. BRD FR-CLI-1 says discovery failures exit 2 "with an explanation". (4) T4c: `validation error: next-week: the instruction contains a broken reference token near "rompt id='blockers' ⟦doccode:repr:9ca966f5e5e9⟧>\n\ns".` The hint (`likely causes: f'{p!r}', …`) makes it actionable, but the snippet is noise, and the hash changes every run (`9ca966f5e5e9` then `d7fe1ccc4e45`), so CI log diffs are noisy. |
| Other: C-new-1 | **yes, most serious** | T4a | Partial f-string removal: `plan` exits 0 and shows `next-week [prompt] <- blockers` (was `<- blockers, in-progress, shipped`); `run --dry-run` exits 0 with `4 nodes ok`; record shows `resolved_instruction` containing `Given what shipped:\n{shipped}\n\nwhat is in progress:\n{wip}`, `warnings=()`. "In CI that's a green build that sends `{shipped}` to a paid model. A literal `{identifier}` that matches a variable/prompt name in an instruction that is already in token mode should be at least a warning, preferably an error." |
| Other: C-new-2 | yes | T2 | HTML is not reproducible: footer `Generated 2026-10-30 13:58 UTC` changes every minute. `.md` and `.json` were byte-stable across runs. "If I commit generated docs, every scheduled run makes a diff. I want `SOURCE_DATE_EPOCH` or a flag to drop the stamp." |
| Other: C-new-3 | yes | T2 | Exit codes overlap: argparse usage errors (`unrecognized arguments: --max-cost 0.50`) exit **2**, same as validation errors; raw tracebacks and render errors (`PDF is not in the spike`) exit **1**, which BRD reserves for "one or more prompts failed". CI can't branch on them. `--help` documents no exit codes and no option help (`--dry-run`, `-j JOBS`, `--entry ENTRY` have no description). |
| Other: C-new-4 | yes | T2 | `run --dry-run` with no `-o` silently wrote `01_weekly_status.md` into the **current directory** (exit 0). In CI that lands in the repo checkout. Expected an error or a documented default. |
| Other: C-new-5 | yes | T4b | `plan` does not validate the `.mock.json` (exit 0 on the typo map); only `run --dry-run` does. And the mock error doesn't say which file it loaded: `config error: Mock response map has keys that match no prompt: 'nextweek' (did you mean 'next-week'?)` / `hint: prompt IDs are printed by doc.plan(); give prompts an explicit id= so renames do not break the map`. Otherwise excellent: exact key, suggestion, exit 3. |
| Other: C-new-6 | yes | T3 | Testability gaps: `help(Document.execute)` → `execute(self, **opts)` (no parameters listed); `RunRecord`/`PromptRecord` undocumented; `ConfigError` importable from `doccode` but not in `__all__`; `plan()` output is text only, so a test that wants "next-week is in the last level" has to parse strings. Example file name `01_…` can't be imported. |
| Other: C-new-7 | observed (not run) | T2 | The example's `__main__` uses CWD-relative paths (`MockProvider("examples/01_weekly_status.mock.json")`, `save("out/weekly.md")`), so `python examples/01_weekly_status.py` only works from `spike/`. The CLI path did **not** have this problem: `--dry-run` found the `.mock.json` beside the script from another CWD. "Fine, CI should use the CLI anyway." |
| Other: C-new-8 | minor | T2 | The summary `4 nodes ok, 220+97 tokens (mock estimate), 0.03s -> …` doesn't say which number is input and which output, and has no cost column, even as `$0.00 (mock)`. |

### CI-relevant behaviour the spike can't show yet (and what P3 expected)

| Need | What happened | What P3 expected |
| --- | --- | --- |
| Spend cap | `--max-cost 0.50` → `unrecognized arguments`, exit 2 | `--budget`/`--max-cost` on `run`, exit 4 when exceeded (BRD FR-CLI-2), and a **cost estimate in `plan`** before a live run |
| Cache | nothing to see; no flag | a cache dir CI can persist between runs, `--no-cache`, and the summary saying "N cached" (FR-CLI-4) |
| Lock / reproducibility | `--frozen` → `unrecognized arguments`, exit 2 | `--frozen` failing with exit 5 when the lock is stale (FR-LCK); a guarantee that `--dry-run` never touches cache or lock (FR-CCH-7), stated in `--help` |
| Failed prompts | can't be triggered: the mock always succeeds; `on_error="continue"` not in the spike | a way to make the mock fail a prompt (e.g. a mock map value that raises), to test exit 1 and partial output in CI |
| Secrets | only "no provider" was testable (exit 3) | the missing-key message must never echo the key or env; key read from env only; `--dry-run` must not need the key (it doesn't, good) |
| Machine-readable summary | text line only | `--summary-json` or similar for pipeline annotations (tokens, cost, cached, failed, wall time) |

## Debrief answers (in persona)

1. What does `{highlights}` do? **correct**: "It's a placeholder for the other prompt's output; DocCode fills it after that prompt runs, and it's how it builds the dependency graph."
2. When does a prompt run? **correct**: "At `execute()`, or `run` on the CLI. Creating it just declares a node, like a Make target."
3. Why did Upgrade Guide wait? "Because its text references `highlights`; the plan shows the edge `<- highlights.1`."
4. Most confusing moment: "4a. I removed one `f`, everything was green, and the only clue was the plan quietly losing two arrows. I'd never spot that in a CI log."
5. Expected but missing: budget/max-cost flag and a cost estimate in `plan`; cache and `--frozen` lock; documented exit codes in `--help`; JSON run summary; a deterministic HTML footer; a way to make the mock fail a prompt.
6. Likelihood to use (1-5): **3**. "The DAG + dry-run model is right for CI and most errors are good, with distinct exit codes for config (3) and validation (2). I'd go to 4 once there's a spend cap, a cache I can persist, and the silent-literal-placeholder case is caught."
7. (P2) Notebook: n/a (P3 track).
8. (P3) Trust in `--dry-run` for CI: "For structure, mostly yes: it validates the graph, the placeholders and the mock map, and it ran with no key. Not as a gate yet: (a) it passes the 4a mistake with exit 0; (b) tracebacks and render errors exit 1, same as 'prompts failed'; (c) I need it guaranteed never to touch cache/lock or the network, stated in `--help`; (d) HTML output isn't reproducible. I'd run `plan` as a pre-merge gate today and `run --dry-run` as a smoke test."

## Moderator's top three observations

1. **The worst failure is silent, not loud.** A half-converted f-string (plain string implicitly concatenated with an f-string) sends `{shipped}` and `{wip}` to the model literally and exits 0 in both `plan` and `run --dry-run`. The full mistake (C2) is caught well. Recommend: in an instruction that already contains reference tokens, a `{identifier}` that names no ctx key should raise `UnknownPlaceholderError` (or at least warn), the same as in placeholder mode. Also print the edge count in `plan` so a lost dependency is visible.
2. **Exit codes are the CI contract and the spike only half honours FR-CLI-2.** Config = 3 and validation = 2 work and are well worded. But argparse usage errors also exit 2, missing-file / bad `--entry` are raw tracebacks with exit 1, and render errors exit 1. That supports keeping FR-CLI-2 as written, and adding: a usage error gets its own code (or is documented as 2), every discovery failure is a one-line message with exit 2 (FR-CLI-1), no tracebacks unless `--debug`, and the codes are listed in `--help`.
3. **Testing the document from pytest is harder than running it.** TTFD was well under target (~5:30) and the CLI dry run took ~3 minutes, but the pytest task overran (~9 vs 5 min) because the run record and `execute()` options aren't documented and `plan()` is text only. Supports: a documented `RunRecord` with order/timing per prompt, a structured `Plan` object (levels, edges) with `str()` for humans, error classes in `__all__`, and an example test in QUICKSTART or the examples (UC-6). Also: deterministic HTML (drop or pin the `Generated …` stamp) and refuse to write output to CWD silently when `-o` is missing.

## Appendix: verbatim outputs

Task 1, `uv run python release_notes.py` (exit 0):
```
warning: Ignoring invalid `SSL_CERT_FILE`. Path does not exist: .
Plan for 'Release Notes': 2 nodes
level 1:
  highlights.1 [prompt, generated id]
level 2:
  upgrade-guide.1 [prompt, generated id] <- highlights.1
```
(The `SSL_CERT_FILE` warning comes from `uv` and the host environment, not DocCode. It appears on every command and is omitted below.)

`release-notes.md`:
```
# Release Notes

## Highlights

[mock:highlights.1] Write three highlights for Acme CLI 2.4.

## Upgrade Guide

[mock:upgrade-guide.1] Based on these highlights:
[mock:highlights.1] Write three highlights for Acme C
```

Task 2:
```
$ python -m doccode run examples/01_weekly_status.py -o out/weekly.md -o out/weekly.html   # no key, no --dry-run
config error: No provider configured (the spike has no default live provider).
  hint: run `python -m doccode run <file> --dry-run`, or doccode.configure(provider=MockProvider(...)); for the owner's live call, see spike/README.md (doccode.live.AnthropicProvider)
exit=3

$ python -m doccode run examples/01_weekly_status.py --dry-run -o …/out/weekly.md -o …/out/weekly.html
4 nodes ok, 220+97 tokens (mock estimate), 0.03s -> …/out/weekly.md, …/out/weekly.html
exit=0

$ python -m doccode plan examples/01_weekly_status.py
Plan for 'Platform Team Weekly Status': 4 nodes
level 1:
  shipped [prompt]
  in-progress [prompt]
  blockers [prompt]
level 2:
  next-week [prompt] <- blockers, in-progress, shipped
exit=0

$ python -m doccode plan --mermaid examples/01_weekly_status.py
graph TD
  "blockers" --> "next-week"
  "in-progress" --> "next-week"
  "shipped" --> "next-week"

$ python -m doccode run examples/01_weekly_status.py --dry-run          # no -o
4 nodes ok, 220+97 tokens (mock estimate), 0.00s -> 01_weekly_status.md   # written into CWD
exit=0

$ … -o out/x.pdf     → error: PDF is not in the spike. / hint: save .html and print it, or wait for WP-402       exit=1
$ … -o out/x.txt     → error: cannot infer a format from 'x.txt'. / hint: pass format=                          exit=1
$ … examples/nope.py → Traceback … FileNotFoundError: [Errno 2] No such file or directory: '…/examples/nope.py'  exit=1
$ … --entry nothere  → Traceback … AttributeError: module 'release_notes' has no attribute 'nothere'           exit=1
$ … --max-cost 0.50  → python -m doccode: error: unrecognized arguments: --max-cost 0.50                         exit=2
$ … --frozen         → python -m doccode: error: unrecognized arguments: --frozen                                exit=2
$ … -j 1             → same Markdown as default -j                                                               exit=0
```
Determinism: two dry runs a minute apart gave identical `.md` and `.json`; the `.html` differed only in `<footer><small>Generated 2026-10-30 13:59 UTC</small></footer>`. A dry run from another CWD (`sessions/p3/`) still picked up `examples/01_weekly_status.mock.json`.

Task 3, `uv run --group dev pytest -q user-test/sessions/p3/tests/test_weekly.py`:
```
...                                                                      [100%]
3 passed in 0.01s
```

Task 4:
```
# a  (first f removed only; second line still f"…{blockers}…")   break/a_no_f.py
plan → next-week [prompt] <- blockers        exit=0
run  → 4 nodes ok, 168+97 tokens (mock estimate), 0.00s -> …/out/a.md   exit=0
resolved_instruction: 'You are writing section 4 "Next Week" (Top three priorities).\n\nGiven what shipped:\n{shipped}\n\nwhat is in progress:\n{wip}\n\nand the blockers:\n[mock:blockers] Describe each blocker and who can unblock it.\n\nset Platform\'s top 3 priorities for next week.'
warnings: ()

# a2 (both f removed)   break/a2_no_f_both.py      plan and run: exit=2
validation error: next-week: unknown placeholder {shipped} in the instruction.
  hint: if this is a Python variable holding a Prompt or Value, use an f-string (f"...{shipped}...") or refs={'shipped': shipped}

# b  ("nextweek" key in the mock map)   break/b_typo.py      plan: exit=0 (map not checked); run --dry-run: exit=3
config error: Mock response map has keys that match no prompt: 'nextweek' (did you mean 'next-week'?)
  hint: prompt IDs are printed by doc.plan(); give prompts an explicit id= so renames do not break the map

# c  ({blockers!r})   break/c_repr.py      plan and run: exit=2
validation error: next-week: the instruction contains a broken reference token near "rompt id='blockers' ⟦doccode:repr:9ca966f5e5e9⟧>\n\ns".
  hint: likely causes: f'{p!r}', a case change such as .upper(), slicing, or str(prompt) used outside a prompt/text
```
