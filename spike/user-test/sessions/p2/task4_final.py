# %%
# P2 final notebook: reusable monthly report (Tasks 2-4 combined)
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
def monthly_report(data, month):
    """Build the monthly report for one region. Figures computed here, not by the model."""
    growth = Value(lambda: f"{(data['revenue'] - data['prior_revenue']) / data['prior_revenue']:+.1%}",
                   id="growth")
    doc = Document(f"{data['region']} Monthly Report", context={**data, "month": month})
    with doc.section("Overview") as s:
        s.metric("Revenue", f"${data['revenue'] / 1e6:.1f}M")
        s.metric("Growth", growth)
        s.table(data["top_products"], caption="Top products")
        Prompt(f"Write two sentences about {ctx.month} for {ctx.region}. "
               f"Revenue changed {growth} on the prior month.")
    return doc

# %%
emea = monthly_report(DATA, "October 2026").execute(provider=MockProvider())
Markdown(emea.render("md"))

# %%
apac = monthly_report(APAC, "October 2026").execute(provider=MockProvider())
Markdown(apac.render("md"))

# %%
# what would the model actually be sent? (mock output is truncated)
print(apac.run.prompts[0].resolved_instruction)

# %%
emea.save("emea-report.md"); apac.save("apac-report.md")
