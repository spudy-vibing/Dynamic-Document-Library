"""UC-1: weekly status report. Three independent sections, then "Next week" depends on all three.

Run offline:  python -m doccode run examples/01_weekly_status.py --dry-run -o out/weekly.md -o out/weekly.html
"""

from doccode import Document, Prompt, ctx

NOTES = {
    "shipped": ["Billing export v2", "SSO for EU tenants"],
    "in_progress": ["Search re-index (60%)", "Rate-limit dashboard"],
    "blockers": ["Waiting on legal for data-retention wording"],
}


def build() -> Document:
    doc = Document("Platform Team Weekly Status",
                   context={"team": "Platform", "week": "2026-W44"},
                   purpose="Weekly status for the engineering manager and partner teams.")
    doc.attach("notes", NOTES)

    with doc.section("Shipped", brief="What reached production this week"):
        shipped = Prompt(f"List what {ctx.team} shipped in {ctx.week}, from the attached notes, "
                         "with one line of impact each.", id="shipped")

    with doc.section("In Progress", brief="Work under way and how far along it is"):
        wip = Prompt("Summarise the in-progress work from the attached notes.", id="in-progress")

    with doc.section("Blockers", brief="What is stuck and who can unblock it"):
        blockers = Prompt("Describe each blocker and who can unblock it.", id="blockers")

    with doc.section("Next Week", brief="Top three priorities"):
        Prompt(f"Given what shipped:\n{shipped}\n\nwhat is in progress:\n{wip}\n\n"
               f"and the blockers:\n{blockers}\n\nset {ctx.team}'s top 3 priorities for next week.",
               id="next-week")
    return doc


if __name__ == "__main__":
    from doccode import MockProvider

    content = build().execute(provider=MockProvider("examples/01_weekly_status.mock.json"))
    content.save("out/weekly.md")
    content.save("out/weekly.html")
