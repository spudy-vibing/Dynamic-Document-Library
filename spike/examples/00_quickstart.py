"""The quickstart from spike/user-test/QUICKSTART.md, exactly as testers type it."""

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
    print(open("release-notes.md").read())
