"""B1 naive parallel: one call per section, concurrently, respecting depends_on (EVALS_PROPOSAL §4.3).

Each call sees only its own section (brief, instruction, constraints, data-block note),
the style guide, the data and attachments, and the text of its ``depends_on`` sections.
It never sees the outline, the purpose or any other section. Results are concatenated
in outline order.
"""

from __future__ import annotations

import asyncio
import time

from evals.core.types import LLM, CaseSection, EvalCase, GenerationResult
from evals.generators import common as c

PROMPT = "b1_parallel"


class NaiveParallel:
    name = "b1-parallel"
    logic_version = "1"

    @property
    def version(self) -> str:
        return c.generator_version(self.logic_version, c.load_prompt(PROMPT), c.shared())

    def user_text(self, case: EvalCase, section: CaseSection, dep_texts: dict[str, str]) -> str:
        p = c.load_prompt(PROMPT)
        deps = ""
        if section.depends_on:
            items = "\n\n".join(
                p.render("dependency_item", title=case.section(d).title, text=dep_texts[d].strip())
                for d in section.depends_on
            )
            deps = p.render("dependencies", items=items) + "\n"
        return p.render(
            "user",
            audience=case.audience or "-",
            style=case.style or "-",
            section=c.section_entry(section),
            dependencies=deps,
            fact_rules=c.fact_rules(),
            title=section.title,
        )

    def planned_calls(self, case: EvalCase, visible_chars_per_section: int) -> list[int]:
        base = len(c.system_prompt()) + len(c.context_text(case))
        filler = "x" * visible_chars_per_section
        return [
            base + len(self.user_text(case, s, {d: filler for d in s.depends_on})) for s in case.sections
        ]

    async def generate(self, case: EvalCase, llm: LLM, rep: int) -> GenerationResult:
        t0 = time.perf_counter()
        texts: dict[str, str] = {}
        stops: dict[str, str | None] = {}
        tasks: dict[str, asyncio.Task[None]] = {}

        async def write(section: CaseSection) -> None:
            for dep in section.depends_on:  # dependencies come earlier, so their tasks exist
                await tasks[dep]
            resp = await llm.complete(
                role="generate",
                node_id=section.id,
                system=c.system_prompt(),
                messages=[c.user_message(case, self.user_text(case, section, texts))],
            )
            texts[section.id] = resp.text
            stops[section.id] = resp.call.stop_reason

        for s in case.sections:
            tasks[s.id] = asyncio.ensure_future(write(s))
        done, pending = await asyncio.wait(tasks.values(), return_when=asyncio.FIRST_EXCEPTION)
        failed = [t for t in done if not t.cancelled() and t.exception() is not None]
        if failed:
            for t in pending:
                t.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            for t in tasks.values():  # retrieve every exception so none is reported as unhandled
                if t.done() and not t.cancelled():
                    t.exception()
            raise failed[0].exception()  # type: ignore[misc]

        warnings: list[str] = [f"section {sid}: stopped at max_tokens" for sid, r in stops.items() if r == "max_tokens"]
        sections = [c.section_out(case, s, texts[s.id], warnings) for s in case.sections]
        return c.assemble(self, case, rep, sections, llm, t0, warnings)
