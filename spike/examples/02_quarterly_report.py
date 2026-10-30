from doccode import Document, Prompt, Value, checks, ctx, parse

# BRD §7 reference example, copied verbatim (only this comment and the one at the bottom added).


class QuarterlyReport(Document):
    def __init__(self, company: str, quarter: str, data: dict):
        super().__init__(f"{company} {quarter} Report",
                         context={"company": company, "quarter": quarter},
                         purpose="Quarterly update for the leadership team.",
                         style="Plain business English. Short paragraphs. No hype.",
                         coherence="report")                # frame + shared brief + editor
        self.attach("data", data)                                   # shared, prompt-cached prefix
        growth = Value(lambda: f"{data['revenue'] / data['prior_revenue'] - 1:.0%}", id="growth")
        grounded = checks.numbers_from(data, growth)                # no invented figures; growth runs first
        outline = Prompt("List the 3 themes that matter most in the attached data, as a bulleted list.",
                         id="outline", visible=False)

        with self.section("Executive Summary", brief="Headline results and the three themes") as s:
            s.metric("Revenue", f"${data['revenue']:,}")            # numbers come from data
            s.metric("Growth", growth)
            summary = Prompt(f"Write a 2-paragraph summary of {ctx.company}'s {ctx.quarter}, "
                             f"built around: {outline}. Revenue growth was {growth}.",
                             id="summary", checks=[checks.max_words(180), grounded])

        with self.section("Themes", brief="One subsection per theme, with evidence from the data"):
            def themes(outline_text: str) -> None:                  # runs once the outline exists
                for theme in parse.list_items(outline_text):
                    with self.section(theme):
                        Prompt("Explain the theme {theme} for {company}, using the attached data.",
                               context={"theme": theme}, checks=[grounded])
            self.expand(themes, after=outline, id="by-theme")      # section ID is "themes"

        with self.section("Financial Analysis", brief="Revenue, margin and growth in detail") as s:
            s.table(data["kpis"], caption="Key metrics")
            analysis = Prompt(f"Using {summary} and the attached data, analyse revenue, margin and growth.",
                              id="analysis", effort="low", checks=[grounded])

        if data.get("issues"):
            with self.section("Risks", brief="Open issues and mitigations"):
                Prompt("Assess these issues and propose mitigations: {issues}",
                       id="risks", context={"issues": data["issues"]})

        with self.section("Recommendations", brief="Three actions for next quarter"):
            Prompt(f"Given {summary} and {analysis}, give 3 recommendations for next quarter.",
                   id="recommendations")

DATA = {"revenue": 50_000_000, "prior_revenue": 40_000_000,
        "kpis": [{"metric": "Gross margin", "value": "62%"}, {"metric": "Customers", "value": "1,240"}],
        "issues": ["hiring"]}

def build() -> Document:                   # found by `doccode run` / `doccode plan` (FR-CLI-1)
    return QuarterlyReport("Acme Inc", "Q3 2026", DATA)

if __name__ == "__main__":                 # not run when the CLI imports this file
    report = build()
    print(report.plan())                   # no API calls; "by-theme" shows as size-unknown
    content = report.execute(max_concurrency=4, lock="doccode.lock")
    content.save("q3.md")
    content.save("q3.json")                # no prompt inputs inside (FR-CNT-8)
    content.save("q3.pdf", template="corporate")   # needs the pdf extra (Phase 4)

# Spike: run with `python -m doccode run examples/02_quarterly_report.py --dry-run -o ...`.
# The __main__ block needs a provider (configure(provider=...)) and PDF, neither of which the spike has.
