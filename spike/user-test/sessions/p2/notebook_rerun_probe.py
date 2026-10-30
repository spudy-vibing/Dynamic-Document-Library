# %%
# Cell A: data + doc (analyst splits build across cells)
DATA = {"region": "EMEA", "revenue": 12_400_000, "prior_revenue": 10_000_000,
        "top_products": [{"product": "Widgets", "revenue": "6.1M"},
                         {"product": "Gizmos", "revenue": "4.3M"}]}
from doccode import Document, MockProvider, Prompt, ctx, Value
doc = Document("EMEA Monthly Report", context=DATA)

# %%
# Cell B: section
with doc.section("Overview") as s:
    s.metric("Revenue", DATA["revenue"])
    Prompt(f"Write two sentences about the month for {ctx.region}.")

# %%
# Cell B again (Shift+Enter twice / re-ran after a tweak)
with doc.section("Overview") as s:
    s.metric("Revenue", DATA["revenue"])
    Prompt(f"Write two sentences about the month for {ctx.region}.")

# %%
print(doc.plan())

# %%
content = doc.execute(provider=MockProvider())
print(content.render("md"))

# %%
# run execute again (re-ran the cell)
content = doc.execute(provider=MockProvider())
print("second execute ok")

# %%
help(s.metric)

# %%
help(s.table)

# %%
help(Value)

# %%
help(Document)
