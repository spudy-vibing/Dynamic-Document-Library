# WP-002 user session script (30 minutes, one tester)

Run three sessions, one per persona below. The owner moderates; nobody else in the room helps. Record results in a copy of [`RESULTS_TEMPLATE.md`](RESULTS_TEMPLATE.md) per tester, then summarise them in `docs/decisions/0004-api-spike.md`.

## Personas (BRD §2)

| Slot | Persona | Recruit someone who | Why this persona |
| --- | --- | --- | --- |
| P1 | Python developer / AI engineer | writes Python weekly, has called an LLM API, has **not** seen DocCode | primary user; the §1.5 time-to-first-document target is defined for them |
| P2 | Data scientist / analyst | works mostly in notebooks with dicts/DataFrames, writes recurring reports | tests `ctx`, data blocks, `Value`, and running inside Jupyter (FR-EXE-1) |
| P3 | DevOps / CI owner | owns CI pipelines, comfortable in Python but not an LLM user | tests the CLI, `--dry-run`, mock maps in tests (UC-6), and error messages |

The template designer persona is left out on purpose: the spike tests the Python authoring API, and designers never touch it. If a fourth person is available, give them task D (optional) only.

## Before the session (owner, 10 min)

1. Check out `spike/api` and run `cd spike && uv run python run_examples.py` once, so `uv` has the dependencies cached.
2. Prepare a clean folder for the tester: a fresh clone or `git worktree` of `spike/api`, with `spike/out/` deleted and **no** `release_notes.py`.
3. For P2, open a Jupyter notebook in `spike/` with the kernel from `uv run --with jupyter jupyter lab` (check it imports `doccode`).
4. Print or open `QUICKSTART.md` and `TASKS.md`. Do not show `spike/README.md`, the examples or the BRD.
5. Start screen and audio recording, with the tester's consent.
6. Have a stopwatch ready. Write times as mm:ss from the start of each task.

## Script

| Time | Step | What the owner says or does |
| --- | --- | --- |
| 0:00–0:02 | Intro | "We're testing the library, not you. Please think aloud: say what you expect before you run something and what surprised you after. I can't help during tasks, except to unblock you if you're stuck for more than 3 minutes." |
| 0:02–0:12 | **Task 1: first document (timed)** | Hand over `QUICKSTART.md`. Start the clock when they start reading. Stop it when `release-notes.md` exists and they've opened it. **This is time to first document (BRD §1.5 target: 10 min or less).** If they hit 10:00, note it and help them finish. |
| 0:12–0:24 | **Tasks 2–4 (persona track)** | Hand over `TASKS.md`, their track only. Time each task. Unblock after 3 minutes stuck and mark the task "assisted". |
| 0:24–0:28 | Debrief | Ask the questions below. |
| 0:28–0:30 | Wrap-up | Save their files and recording. Thank them. |

## Debrief questions (ask all, in order)

1. In one sentence, what does `{highlights}` inside the f-string do?
2. When does a prompt actually run?
3. Why did "Upgrade Guide" wait for "Highlights"?
4. What was the most confusing moment?
5. Was there anything you expected to exist and didn't?
6. On a scale of 1–5, how likely are you to use this for a real recurring document? Why?
7. (P2 only) Did running it in a notebook feel natural?
8. (P3 only) Would you trust the `--dry-run` output in CI? What would you need?

Score question 1 and 2: correct if they say it is a placeholder or reference that is filled after the other prompt runs, and that prompts run at `execute()`, not when created.

## What to watch for (tick in the results template)

These are the confusions the spike builder predicted (see 0004 findings). Note each one you see, with the time and the tester's words.

| Code | Watch for | Related finding |
| --- | --- | --- |
| C1 | Prints a prompt (`print(highlights)`) and is confused by `⟦doccode:ref:…⟧` | F3 |
| C2 | Forgets the `f` prefix: `"Based on {highlights}"` → `UnknownPlaceholderError` | F3 |
| C3 | Uses `+` or `.format()` with a prompt (`"x " + highlights`) → `TypeError` | F3 |
| C4 | Uses a format spec on a value: `f"{growth:.1%}"` → `TypeError` | F4 |
| C5 | Expects `Prompt(...)` to return text or run immediately | F2 |
| C6 | Creates a prompt before/outside the `with` block and expects it in the section (look for `OrphanPromptWarning`) | F2 |
| C7 | Has to read `plan()` to discover IDs like `highlights.1` for the mock map; mistypes an ID | F7 |
| C8 | Expansion: does not understand why theme text goes through `context=` rather than an f-string | F5 |
| C9 | Expansion: the run fails because the mock outline is not a list, and they don't know to add a `.mock.json` | F8 |
| C10 | Mixes `{ctx.x}` and `{name}` in one f-string and doesn't notice `{name}` is sent literally | F3 |
| C11 | Passes a prompt in `context=` (spike raises `TokenMisuseError`) | F6 |
| C12 | Confused by `visible=False` vs "created outside a section" (two ways to make a helper) | F2 |
| C13 | Tries to give a section an ID matching a prompt ID (or vice versa) | F1 |
| C14 | Expects `Document.execute()` to work with no provider (spike: `ConfigError`) | — |
| C15 | Any error message they couldn't act on — copy the message verbatim | FR-ERR-2 |

Also note anything not on this list: that's the most valuable data.

## After all three sessions (owner, about 1 hour)

1. Fill §"User sessions" in `docs/decisions/0004-api-spike.md` from the three result sheets: time to first document per tester, task completion, the confusion codes seen, quotes.
2. Make the live call (spike `README.md`, "Owner's live call") and fill §"Owner's live call".
3. Decide on each recommended BRD change in 0004 (accept / reject / modify). Amend the BRD for the accepted ones. **WP-003 must not start until you have approved these amendments.**
