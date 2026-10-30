"""P1 prototype: B1 + the document frame (FR-COH-1; EVALS_PROPOSAL §4.3). Approximates the ``basic`` preset.

Every section call carries the byte-identical outline frame (title, purpose, audience,
style, every section's title and brief, then the no-repeat line) in its shared prefix,
and a position line at the start of its per-prompt part. Scheduling is B1's: one call
per section, concurrent where ``depends_on`` allows, dependencies' text in the per-prompt
part. Repetition detection (FR-COH-5, part of ``basic``) runs afterwards as warnings.
"""

from __future__ import annotations

import time

from evals.core.types import LLM, EvalCase, GenerationResult, SectionOut
from evals.generators import coherence as co
from evals.generators import common as c


def finish(generator, case: EvalCase, rep: int, sections: list[SectionOut], llm: LLM, t0: float,
           warnings: list[str]) -> GenerationResult:
    """Repetition findings (FR-COH-5) become warnings; then the shared assembly."""
    warnings += [f.warning() for f in co.find_repetition(co.model_texts(sections))]
    return c.assemble(generator, case, rep, sections, llm, t0, warnings)


class FrameParallel:
    name = "p1-frame"
    logic_version = "1"

    @property
    def version(self) -> str:
        return c.generator_version(self.logic_version, co.prompt(co.FRAME), c.shared())

    def planned_calls(self, case: EvalCase, visible_chars_per_section: int) -> list[int]:
        base = co.shared_chars(case)
        filler = "x" * visible_chars_per_section
        return [base + len(co.section_text(case, s, co.dependencies_text(case, s, {d: filler for d in s.depends_on})))
                for s in case.sections]

    async def generate(self, case: EvalCase, llm: LLM, rep: int) -> GenerationResult:
        t0 = time.perf_counter()
        draft = await co.write_parallel(case, llm)
        warnings = draft.warnings()
        sections = [c.section_out(case, s, draft.texts[s.id], warnings) for s in case.sections]
        return finish(self, case, rep, sections, llm, t0, warnings)
