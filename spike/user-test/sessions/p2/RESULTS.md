# WP-002 session results: tester P2-sim (P2)

> **SIMULATED SESSION (agent playing P2) — weaker evidence than a real user.**
> An AI agent played the P2 persona (data analyst, notebook user, not a software engineer, has used ChatGPT but no LLM library). It used only QUICKSTART.md, TASKS.md (P2 track), and what `help()` / `dir()` show in a notebook. It did not read the library source, `spike/README.md`, the examples or 0004. Code did run for real: notebook cells ran on a real IPython kernel (via `nbclient`; executed `.ipynb` files are next to each `.py` cell file). Times are **estimates** of what a human in this persona would take, not stopwatch readings. The agent knows Python better than P2 does, so it probably found fixes faster than a real analyst would.

- Date: 2026-10-30
- Moderator: none (self-run by the agent)
- Tester background (simulated): pandas and notebooks daily, basic Python (functions yes, classes rarely, f-strings yes, `async` no), ChatGPT web UI only, no LLM API or library.
- Environment: macOS (Darwin 25.6), `uv` already installed, Python from `uv` (CPython 3.14), IPython kernel run headless by `nbclient` with `ipykernel` (stands in for JupyterLab). Task 1 was run in a terminal, as QUICKSTART tells you to.
- Recording saved at: n/a. Evidence is the files in this folder:
  - `release_notes.py`, `release-notes.md`, `release-notes.html`: Task 1
  - `task2_attempt{1,2,3}.py/.ipynb`: Task 2
  - `task3_attempt{1,2}.py/.ipynb`: Task 3
  - `task4_attempt1.py/.ipynb`, `task4_final.py/.ipynb`, `emea-report.md`, `apac-report.md`: Task 4
  - `notebook_rerun_probe.py/.ipynb`, `mockprobe.py/.ipynb`, `mockprobe.mock.json`, `probe_plain_string.py`: probes of notebook behaviour (see "Other")

Deviation from the script: QUICKSTART says to create `release_notes.py` in `spike/`. To keep all session files in one folder, it was created in `spike/user-test/sessions/p2/` and run from there (`uv run` finds the project by searching parent folders). The behaviour is the same.

## Timings

All times are estimated mm:ss for a human P2. They are not measured.

| Task | Start | End | Duration | Completed? (yes / assisted / no) | Notes |
| --- | --- | --- | --- | --- | --- |
| 1. First document (QUICKSTART) | 0:00 | ~7:30 | **TTFD: ~7:30** (range 6–9 min with `uv` already installed; ~11–14 min if P2 must install `uv` first) | yes | Ran first time, no errors. Time went on reading, the terminal, making a `.py` file outside Jupyter, and working out the output. Steps: (1) read the page, (2) find and `cd spike`, (3) `uv sync`, (4) make a `.py` file and paste the code, (5) `uv run python release_notes.py`, (6) read the plan printout, (7) open the `.md`, (8) open the `.html`. 6 hesitations (listed below). |
| 2. Data report (Overview: metric, table, prompt using region from data) | ~0:00 | ~8:30 | ~8:30 (budget 5) | yes, over time | Building the document was fast (~3 min, copied QUICKSTART). "Look at the Markdown" took ~4–5 min of guessing: `content` in a cell prints a huge dataclass repr; `to_markdown()` and `.markdown` raise `AttributeError`; found `render` with `dir()`; `help(content.render)` shows only `render(format: str, ...)`, so the format string `"md"` was guessed from the file extension. Showing it as Markdown needed `IPython.display.Markdown`, which many analysts don't know. Revenue metric showed as `12400000`, so P2 formatted it by hand. |
| 3. Computed growth as a metric, used in the prompt | ~0:00 | ~5:00 | ~5:00 (budget 3) | yes | Copied the QUICKSTART `Value(lambda: ...)` pattern, then wrote `{growth:.1%}`, which hit C4. The error message was clear and the fix took ~1 min. Then: "why do I need `Value` at all? A normal variable works." Tried the computed figure through `ctx`, and the metric printed a raw token (see Other-1). |
| 4. Reuse for any region and month | ~0:00 | ~6:30 | ~6:30 (budget 4) | yes (borderline; a real P2 might have needed the 3-min unblock) | Wrapped it in a function `monthly_report(data, month)`. The first try kept the `growth` Value at notebook top level, and the second region failed with `UnknownReferenceError ... (FR-REF-4)`. P2 guessed that moving the Value inside the function would help, and it did. Built EMEA (+24.0%) and APAC (−10.0%). Nobody tried a class: QUICKSTART never shows subclassing. |

**Time to first document (BRD §1.5, target ≤ 10:00):** ~7:30 (estimated) → **met**, but only if `uv` is already installed. A notebook analyst often doesn't have `uv`; installing it plus restarting the shell would probably push this past 10:00.

### Task 1 hesitation log (P2 view)

1. "`cd spike`: from where?" The page assumes you are in the repo root in a terminal. P2 lives in JupyterLab.
2. "Create `release_notes.py`": P2 would rather paste it into a notebook cell. The page gives no notebook path, even though P2 is the notebook persona. They make a `.py` in JupyterLab's text editor (~1 min).
3. The `warning: Ignoring invalid SSL_CERT_FILE` line from `uv` (from this machine's environment) looks like an error to a non-engineer. Not DocCode's fault, but it costs a moment.
4. Plan output: `level 1:`, `2 nodes`, `[prompt, generated id]`, `<- highlights.1`. P2: "what is a node? what is level?" They understood it after seeing the `<-` arrow.
5. Mock output: `[mock:upgrade-guide.1] Based on these highlights:\n[mock:highlights.1] Write three highlights for Acme C`. The words are cut off ("Acme C"), so P2 thought the run had broken. The text box under "What just happened" explains the mock, but not the truncation.
6. "Open them": P2 double-clicks the `.html` in Finder, which is fine. The `.md` shows as raw text in an editor unless opened in JupyterLab.

## Confusions seen

| Code | Seen? | Time | What they said or did |
| --- | --- | --- | --- |
| C1 print shows a token | **yes (variant)** | T3 ~1:00 | Inspected the Value the analyst way: `growth` in a cell gives `<Value id='growth' ⟦doccode:repr:2e9d41574402⟧>`, and `print(growth)` gives `⟦doccode:ref:2e9d41574402⟧`. "I computed 24%, why can't I see it?" |
| C2 forgot the `f` | no | | Not seen naturally. A probe (`probe_plain_string.py`) found that a plain string with `{region}` works anyway when `region` is in `context=`, so forgetting the `f` is often *harmless* for P2. That makes it harder to know which style is "right" (see Other-7). |
| C3 `+` / `.format()` with a prompt | no | | |
| C4 format spec on a Value | **yes** | T3 ~0:45 | `f"... Revenue grew {growth:.1%}."` → `TypeError: value growth was formatted with '.1%'; references take no format spec. For a Value, format inside the function instead.` **Actionable**: fixed in ~1 min by formatting inside the lambda. P2 still thought it was odd: "it's a number, `.1%` is how you format numbers." |
| C5 expected Prompt to run at once | partly | T3 | Did not expect a Prompt to return text, but *did* expect a `Value` to hold its number at once (see C1). Analysts treat `Value(lambda: ...)` like a computed column. |
| C6 prompt outside the `with` block | no | | Values were made outside the `with` block, and that worked. No `OrphanPromptWarning` was seen. |
| C7 had to discover generated IDs | yes (mild) | T1, probe | The mock map needs `overview.1`. P2 found it from the plan or from the `[mock:overview.1]` prefix, which is fine. |
| C8 expansion: why `context=` | n/a | | P2 track does not use expansion. |
| C9 expansion failed without a mock map | n/a | | |
| C10 `{name}` sent literally in an f-string | no | | P2 mixed `{ctx.region}` with plain Python values (`{data['revenue']}`) in f-strings. Those are real values, so nothing was sent literally. |
| C11 prompt passed in `context=` | no | | |
| C12 two kinds of helper prompt | n/a | | |
| C13 section/prompt ID clash | no | | Duplicate section titles were *silently* allowed instead (see Other-2). |
| C14 no provider configured | **yes** (probe) | mockprobe | ChatGPT habit: `doc.execute()` with no provider → `ConfigError: No provider configured (the spike has no default live provider).  hint: run \`python -m doccode run <file> --dry-run\`, or doccode.configure(provider=MockProvider(...)); for the owner's live call, see spike/README.md (doccode.live.AnthropicProvider)`. The first suggestion (a CLI on a file) does not apply in a notebook. The second one works. |
| C15 unactionable error message (paste it) | **yes ×2** | T4 ~2:00; notebook | (a) `UnknownReferenceError: value growth belongs to another document ('EMEA Monthly Report').  hint: tokens are document-scoped (FR-REF-4)`. P2: "what's a token? what's FR-REF-4? I only made one `growth`." It doesn't say "create the Value inside the function that builds each document". (b) `DocumentFrozenError: Document 'EMEA Monthly Report' is frozen: execute() has started.  hint: only an expansion's build function may add to a running document; use doc.copy() for a re-run with changes (not in the spike)`. This came up when touching `doc.section(...)` in a new cell after running. "Expansion"? And the suggested fix doesn't exist. |
| Other: | yes | | See below. |

### Other (not on the list; most valuable)

- **Other-1, silent token leak in output (bug-like, C-new).** `s.metric("Growth", ctx.growth)` (computed figure put in `context=`, then used via `ctx` like in prompts) raises nothing, and the **final rendered document shows `**Growth:** ⟦doccode:ctx:growth⟧`**. The same `ctx.growth` inside a Prompt f-string resolved correctly. P2 would only notice this when reading the output, or not at all if the report is mailed automatically. (`task3_attempt2.ipynb`, cell 3.)
- **Other-2, re-running a notebook cell silently duplicates a section (C-new, notebook).** Running the `with doc.section("Overview")` cell twice (normal in Jupyter) gives two "Overview" sections, `overview.1` and `overview-2.1`, with no warning. Once `execute()` has run, adding to the same `doc` raises `DocumentFrozenError` (C15b). So the notebook rule is "always re-run the whole build from `Document(...)` down", and nothing says this. (`notebook_rerun_probe.ipynb`.)
- **Other-3, no notebook display for the result (C-new, notebook).** `content` as the last line of a cell shows a ~2,000-character `DocumentContent(...)` dataclass repr with run records and token counts. There is no `_repr_markdown_`/`_repr_html_`, no `to_markdown()`, and `render()`'s format names are not documented. P2 expected the cell to just show the report.
- **Other-4, `help()` is almost empty (C-new).** `help(content.render)`, `help(content.save)`, `help(s.metric)` and `help(s.table)` show only signatures. `help(Value)` says `FR-VAL-1: Python compute node. fn(*dep_results).`, which is spec jargon. For P2, docstrings are the main docs after QUICKSTART.
- **Other-5, `Value` looks unnecessary for data figures (C-new, conceptual).** A plain Python variable (`growth_pct = ...; s.metric("Growth", f"{growth_pct:.1%}")`, and `{growth_pct:.1%}` in the prompt f-string) works, is shorter, allows format specs, and can be inspected. QUICKSTART's only Value example is `Value(lambda: "25%")`, so P2 read it as "the way to do computed numbers". When P2 then did it the simple way: "so what is Value for?"
- **Other-6, the `<script>.mock.json` convention does nothing in a notebook (C-new, notebook).** `mockprobe.mock.json` next to `mockprobe.ipynb` was ignored without a message, and the mock gave placeholder text. The dict form `MockProvider({"overview.1": ...})` worked. QUICKSTART presents both as equivalent.
- **Other-7, three ways to put data in a prompt (C-new).** `f"...{ctx.region}"` (QUICKSTART main example), `"...{region}"` with `context=` (QUICKSTART expansion example, no `f`), and plain Python `f"...{data['region']}"`. All three give the same result. `"{DATA['region']}"` without `f` → `UnknownPlaceholderError: overview.1: placeholder {DATA['region']} is not a plain name.  hint: placeholders are {name}; write literal braces as {{ }}`. P2 couldn't tell which one was "the DocCode way" or why `ctx` exists when a plain f-string with `data[...]` works.
- **Other-8, mock output is cut off (C-new, minor).** `[mock:overview.1] ... Revenue changed +24.0% on the p`. P2 wanted to check that the prompt said the right thing, and the mock cuts it off. Seeing the full prompt needed `content.run.prompts[1].resolved_instruction`, found by reading the repr. And `run.prompts[0]` is the *Value* record (`resolved_instruction=None`), which confused "prompts".
- **Other-9, metric values are not formatted.** `s.metric("Revenue", 12_400_000)` shows `12400000`. P2 expected thousands separators, or at least a `format=`/`unit=` example. `unit=` exists but is not documented.

## Debrief answers (in persona)

1. What does `{highlights}` do? **correct**: "It's a placeholder. DocCode fills it with the highlights text after that prompt has run."
2. When does a prompt run? **correct**: "When I call `execute()`, not when I write `Prompt(...)`." (But P2 believed a `Value` *should* compute at once; see C1/C5.)
3. Why did Upgrade Guide wait? "Because its prompt uses `{highlights}`, so it needs that answer first. The plan arrow showed it."
4. Most confusing moment: "Getting the report to show up in the notebook, and then `⟦doccode:ctx:growth⟧` appearing in my metric with no error."
5. Expected but missing: show the report when `content` is the last line of a cell; `content.to_markdown()`; format specs on numbers (`{growth:.1%}`); pandas DataFrame in `s.table(...)` (not tried; the data was a list of dicts); a notebook version of QUICKSTART; docstrings.
6. Likelihood to use (1–5): **3**. "The metric and table blocks are exactly what my monthly report needs, and the function version reruns per region nicely. But I'd worry about wrong output nobody notices (the token in the metric), and I'd need a notebook-first guide."
7. (P2) Notebook: "`execute()` just worked in the notebook, no `await`, which is good. Re-running cells duplicates sections or says 'frozen', the result doesn't display, and the `.mock.json` trick doesn't work. It felt like a script library used from a notebook, not a notebook library."
8. (P3) n/a.

## Moderator's top three observations

1. **Notebook ergonomics are the biggest gap for P2, not the reference model.** FR-EXE-1 works (sync `execute()` inside a running kernel loop, no `await`). Around it: no rich display of `DocumentContent`, re-running a cell silently duplicates sections, `DocumentFrozenError` after a run, `.mock.json` ignored, and QUICKSTART is terminal-only. Task 2 ran ~70% over its time budget almost entirely because of "how do I see the Markdown".
2. **`ctx` used outside a Prompt leaks a raw token into the rendered document with no error** (`s.metric("Growth", ctx.growth)` → `⟦doccode:ctx:growth⟧`). This is the only *silent wrong output* found, and the most dangerous for a recurring report. It should either resolve or raise.
3. **`Value` doesn't earn its place in data-only reports, and its errors use internal words.** Plain Python did everything P2 needed and allowed format specs. The Value path caused C4, the C1-style "can't see my number", and the FR-REF-4 error when reused across documents. Error hints that cite FR codes or features not in the spike (`doc.copy()`) can't be acted on. The C4 message is the model to follow: it says what to do.
