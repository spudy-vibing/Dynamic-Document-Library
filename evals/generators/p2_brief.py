"""P2 prototype: P1 + the shared brief (FR-COH-2; EVALS_PROPOSAL §4.3). Approximates ``report`` without the editor.

A ``_brief`` call runs first. It sees the frame, the data and attachments (the same
prefix every section call starts with) and writes Markdown under four fixed headings:
Core message, Section points (one bullet per section ID), Terms, Avoid. The brief then
goes into every section call's shared prefix, after the attachments, with the cache
breakpoint on it; the sections run as in P1. Calls: 1 + one per section, critical path
1 + the longest depends_on chain.
"""

from __future__ import annotations

import time

from evals.core.types import LLM, EvalCase, GenerationResult
from evals.generators import coherence as co
from evals.generators import common as c
from evals.generators.p1_frame import finish


class BriefParallel:
    name = "p2-brief"
    logic_version = "1"

    @property
    def version(self) -> str:
        return c.generator_version(self.logic_version, co.prompt(co.FRAME), co.prompt(co.BRIEF), c.shared())

    def planned_calls(self, case: EvalCase, visible_chars_per_section: int) -> list[int]:
        brief_call = co.shared_chars(case) + len(co.brief_instruction(case))
        base = co.shared_chars(case, brief_chars=co.BRIEF_CHARS_ESTIMATE)
        filler = "x" * visible_chars_per_section
        return [brief_call] + [
            base + len(co.section_text(case, s, co.dependencies_text(case, s, {d: filler for d in s.depends_on})))
            for s in case.sections
        ]

    async def generate(self, case: EvalCase, llm: LLM, rep: int) -> GenerationResult:
        t0 = time.perf_counter()
        warnings: list[str] = []
        brief = await co.write_brief(case, llm, warnings)
        draft = await co.write_parallel(case, llm, brief=brief)
        warnings += draft.warnings()
        sections = [c.section_out(case, s, draft.texts[s.id], warnings) for s in case.sections]
        return finish(self, case, rep, sections, llm, t0, warnings)
