# %%
DATA = {"region": "EMEA", "revenue": 12_400_000, "prior_revenue": 10_000_000,
        "top_products": [{"product": "Widgets", "revenue": "6.1M"},
                         {"product": "Gizmos", "revenue": "4.3M"}]}
APAC = {"region": "APAC", "revenue": 8_100_000, "prior_revenue": 9_000_000,
        "top_products": [{"product": "Gizmos", "revenue": "3.9M"},
                         {"product": "Widgets", "revenue": "2.2M"},
                         {"product": "Doohickeys", "revenue": "1.0M"}]}
from doccode import Document, MockProvider, Prompt, ctx, Value
from IPython.display import Markdown

# %%
# First try: keep the Value from earlier at top level, function builds the doc
growth = Value(lambda: f"{(DATA['revenue'] - DATA['prior_revenue']) / DATA['prior_revenue']:.1%}", id="growth")

def monthly_report(data, month):
    doc = Document(f"{data['region']} Monthly Report", context={**data, "month": month})
    with doc.section("Overview") as s:
        s.metric("Revenue", f"${data['revenue'] / 1e6:.1f}M")
        s.metric("Growth", growth)
        s.table(data["top_products"], caption="Top products")
        Prompt(f"Write two sentences about {ctx.month} for {ctx.region}. Revenue grew {growth} on the prior month.")
    return doc

emea = monthly_report(DATA, "October 2026").execute(provider=MockProvider())
apac = monthly_report(APAC, "October 2026").execute(provider=MockProvider())
print(emea.render("md"))
print(apac.render("md"))
