# DocCode quickstart (preview)

DocCode lets you write a multi-section document in Python. Each section holds prompts. Prompts can use each other's output, and DocCode runs them in the right order (in parallel where it can), then renders Markdown or HTML.

This preview runs **offline**: a mock model returns placeholder text, so you can see the structure without an API key.

## 1. Set up (1 minute)

```bash
cd spike
uv sync
```

## 2. Write your first document

Create `release_notes.py` in `spike/`:

```python
from doccode import Document, MockProvider, Prompt, ctx

doc = Document("Release Notes", context={"product": "Acme CLI", "version": "2.4"})

with doc.section("Highlights"):
    highlights = Prompt(f"Write three highlights for {ctx.product} {ctx.version}.")

with doc.section("Upgrade Guide"):
    Prompt(f"Based on these highlights:\n{highlights}\n\nwrite short upgrade steps.")

if __name__ == "__main__":
    print(doc.plan())
    content = doc.execute(provider=MockProvider())
    content.save("release-notes.md")
    content.save("release-notes.html")
```

## 3. Run it

```bash
uv run python release_notes.py
```

You now have `release-notes.md` and `release-notes.html`. Open them.

## What just happened

- **Sections.** `with doc.section("Highlights"):` opens a section. A `Prompt` created inside it becomes that section's content.
- **References.** `{highlights}` in an f-string does not paste text now (the prompt hasn't run yet). It leaves a marker that DocCode fills in with the highlights once they exist. That is also how DocCode knows "Upgrade Guide" must wait for "Highlights".
- **Context.** `{ctx.product}` is filled from `context=` on the document, a section or a prompt.
- **The plan.** `doc.plan()` shows what runs in which order, with no model calls. Each prompt has an ID (`highlights.1`); pass `id="..."` to choose your own.
- **The mock.** `MockProvider()` answers every prompt with `[mock:<id>] <start of the prompt>`. To give a prompt a realistic answer, pass a dict: `MockProvider({"highlights.1": "- Faster startup\n- ..."})`, or put that dict in a JSON file next to your script named `<script>.mock.json`.

## A few more things you can do

```python
from doccode import Value, checks, parse

doc.attach("data", {"revenue": 50_000_000})            # shared input every prompt can see

growth = Value(lambda: "25%", id="growth")              # plain Python between prompts
with doc.section("Numbers") as s:
    s.metric("Growth", growth)                          # figures come from data, not the model
    s.table([{"metric": "Customers", "value": "1,240"}], caption="KPIs")
    Prompt(f"Comment on growth of {growth}.", checks=[checks.max_words(120)])

outline = Prompt("List three themes as bullets.", id="outline", visible=False)   # helper: no output block
with doc.section("Themes"):
    def per_theme(text):                                 # runs after `outline` has an answer
        for theme in parse.list_items(text):
            with doc.section(theme):
                Prompt("Explain {theme}.", context={"theme": theme})
    doc.expand(per_theme, after=outline, id="by-theme")
```

There is also a command line: `uv run python -m doccode plan release_notes.py` and `uv run python -m doccode run release_notes.py --dry-run -o out.md -o out.html` (it looks for a function `build()` that returns the document, or a module-level `doc`).
