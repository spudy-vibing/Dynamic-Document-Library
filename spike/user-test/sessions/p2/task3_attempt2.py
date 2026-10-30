# %%
DATA = {"region": "EMEA", "revenue": 12_400_000, "prior_revenue": 10_000_000,
        "top_products": [{"product": "Widgets", "revenue": "6.1M"},
                         {"product": "Gizmos", "revenue": "4.3M"}]}
from doccode import Document, MockProvider, Prompt, ctx, Value
from IPython.display import Markdown

# %%
# hint said: format inside the function
growth = Value(lambda: f"{(DATA['revenue'] - DATA['prior_revenue']) / DATA['prior_revenue']:.1%}", id="growth")

doc = Document("EMEA Monthly Report", context=DATA)
with doc.section("Overview") as s:
    s.metric("Revenue", f"${DATA['revenue'] / 1e6:.1f}M")
    s.metric("Growth", growth)
    s.table(DATA["top_products"], caption="Top products")
    Prompt(f"Write two sentences about the month for {ctx.region}. Revenue grew {growth} on the prior month.")

content = doc.execute(provider=MockProvider())
Markdown(content.render("md"))

# %%
# analyst's simpler idea: why not just a normal Python variable?
growth_pct = (DATA["revenue"] - DATA["prior_revenue"]) / DATA["prior_revenue"]

doc2 = Document("EMEA Monthly Report", context=DATA)
with doc2.section("Overview") as s:
    s.metric("Revenue", f"${DATA['revenue'] / 1e6:.1f}M")
    s.metric("Growth", f"{growth_pct:.1%}", delta=f"+{growth_pct:.1%}")
    s.table(DATA["top_products"], caption="Top products")
    Prompt(f"Write two sentences about the month for {ctx.region}. Revenue grew {growth_pct:.1%} on the prior month.")

content2 = doc2.execute(provider=MockProvider())
Markdown(content2.render("md"))

# %%
# can I put the computed number in context and use ctx?
doc3 = Document("EMEA Monthly Report", context={**DATA, "growth": f"{growth_pct:.1%}"})
with doc3.section("Overview") as s:
    s.metric("Growth", ctx.growth)
    Prompt(f"Write two sentences about the month for {ctx.region}. Revenue grew {ctx.growth}.")
print(doc3.execute(provider=MockProvider()).render("md"))
