# Tasks (hand the tester their track only)

Everyone does Task 1 from `QUICKSTART.md` first. Then do your track's tasks in order. Think aloud. If you're stuck for 3 minutes, say so.

---

## Track P1: Python developer

**Task 2: weekly status (UC-1), about 5 min.** In a new file `weekly.py`, build a document "Team Weekly Status" with three sections, "Shipped", "In Progress" and "Blockers", each with one prompt. Add a fourth section, "Next Week", whose prompt uses the output of all three. Run it with the mock and check the plan shows "Next Week" running last.

**Task 3: give one prompt a real answer, about 3 min.** Make the mock answer the "Shipped" prompt with a two-item bulleted list of your choice. The other prompts keep the default mock text.

**Task 4: one section per item, about 4 min.** Add a hidden prompt that lists three risks as bullets. Add a section "Risks" that gets one subsection per risk, each with its own prompt. Make the mock return a real list so it works.

---

## Track P2: Data analyst (in a notebook)

Use the open Jupyter notebook. Here is your data; paste it into a cell:

```python
DATA = {"region": "EMEA", "revenue": 12_400_000, "prior_revenue": 10_000_000,
        "top_products": [{"product": "Widgets", "revenue": "6.1M"},
                         {"product": "Gizmos", "revenue": "4.3M"}]}
```

**Task 2: a data report, about 5 min.** Build "EMEA Monthly Report" with a section "Overview" that shows the revenue as a metric and the products as a table, then a prompt that writes two sentences about the month for the region (take the region name from the data, not typed into the prompt). Run it in the notebook and look at the Markdown.

**Task 3: computed figure, about 3 min.** Compute the growth rate in Python (not by the model) and show it as a second metric. Then make the prompt mention it.

**Task 4: reuse, about 4 min.** Turn your document into something you could run for any region and month, for example a function or a class that takes `DATA`. Build it for a second region with different numbers.

---

## Track P3: DevOps / CI owner (terminal)

You've been handed `examples/01_weekly_status.py` by a colleague (open it).

**Task 2: run it like CI would, about 3 min.** Without editing the file, produce `out/weekly.md` and `out/weekly.html` from the command line, without any API key. Then show the execution plan.

**Task 3: a test, about 5 min.** Write a pytest test in `tests/test_weekly.py` that builds this document, runs it with the mock, and asserts that the "Next Week" section exists and that the "Next Week" prompt ran after the other three. Run `uv run --group dev pytest -q tests/test_weekly.py`.

**Task 4: break it on purpose, about 4 min.** In a copy of the example, make each of these mistakes, one at a time, and run it. For each, say whether the error message tells you what to fix:
  a. Remove the `f` in front of the "Next Week" prompt string.
  b. Add a key `"nextweek"` (typo) to `01_weekly_status.mock.json`.
  c. Change `{blockers}` in the "Next Week" prompt to `{blockers!r}`.

---

## Optional track D: template designer (only if a fourth person is available)

Given `out/01_weekly_status.html`, change its look (fonts, colours, a header) without editing any Python other than adding `template="my_template.html.j2"` to the `run` call. Hint for the owner: the default template is `doccode.render.DEFAULT_TEMPLATE`; let them discover it.
