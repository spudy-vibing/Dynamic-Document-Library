# Probe: copy the QUICKSTART expansion style (plain string + context=) for the region.
# Not a natural first move in this session; checks a path the QUICKSTART invites.
from doccode import Document, MockProvider, Prompt

DATA = {"region": "EMEA", "revenue": 12_400_000, "prior_revenue": 10_000_000,
        "top_products": [{"product": "Widgets", "revenue": "6.1M"}]}

for label, make in [
    ("A: plain string, context on Prompt", lambda: Prompt("Write two sentences about the month for {region}.", context=DATA)),
    ("B: plain string, context on Document", lambda: Prompt("Write two sentences about the month for {region}.")),
    ("C: plain string with DATA['region']", lambda: Prompt("Write two sentences about the month for {DATA['region']}.")),
]:
    print("=====", label)
    try:
        doc = Document("EMEA Monthly Report", context=DATA)
        with doc.section("Overview"):
            make()
        print(doc.execute(provider=MockProvider()).render("md"))
    except Exception as e:
        print(f"{type(e).__name__}: {e}")
