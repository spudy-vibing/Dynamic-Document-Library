# %%
from doccode import Document, MockProvider, Prompt, ctx
doc = Document("EMEA Monthly Report", context={"region": "EMEA"})
with doc.section("Overview"):
    Prompt(f"Write two sentences about the month for {ctx.region}.")

# %%
# ChatGPT habit: just run it
doc.execute()

# %%
# QUICKSTART: "put that dict in a JSON file next to your script named <script>.mock.json"
# In a notebook, the 'script' is mockprobe.ipynb -> tried mockprobe.mock.json next to it
doc2 = Document("EMEA Monthly Report", context={"region": "EMEA"})
with doc2.section("Overview"):
    Prompt(f"Write two sentences about the month for {ctx.region}.")
print(doc2.execute(provider=MockProvider()).render("md"))

# %%
# dict form works?
doc3 = Document("EMEA Monthly Report", context={"region": "EMEA"})
with doc3.section("Overview"):
    Prompt(f"Write two sentences about the month for {ctx.region}.")
print(doc3.execute(provider=MockProvider({"overview.1": "EMEA had a strong month."})).render("md"))
