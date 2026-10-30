# %%
DATA = {"region": "EMEA", "revenue": 12_400_000, "prior_revenue": 10_000_000,
        "top_products": [{"product": "Widgets", "revenue": "6.1M"},
                         {"product": "Gizmos", "revenue": "4.3M"}]}
from doccode import Document, MockProvider, Prompt, ctx, Value

# %%
# copy the quickstart pattern: Value for a figure computed in Python
growth = Value(lambda: (DATA["revenue"] - DATA["prior_revenue"]) / DATA["prior_revenue"], id="growth")

doc = Document("EMEA Monthly Report", context=DATA)
with doc.section("Overview") as s:
    s.metric("Revenue", DATA["revenue"])
    s.metric("Growth", growth)
    s.table(DATA["top_products"], caption="Top products")
    Prompt(f"Write two sentences about the month for {ctx.region}. Revenue grew {growth:.1%}.")

# %%
# what is growth? (analyst habit: inspect the variable)
growth

# %%
print(growth)
