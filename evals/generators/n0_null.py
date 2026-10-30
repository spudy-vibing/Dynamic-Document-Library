"""N0 null baseline (EVALS_PROPOSAL §6.4): section titles and harness-built data blocks only.

No model text and no LLM calls. Judges must score it at or near the floor, and every
deterministic grader must handle it without errors.
"""

from __future__ import annotations

from evals.core.render import data_blocks_for, finalize
from evals.core.types import LLM, EvalCase, GenerationResult, SectionOut


class NullBaseline:
    name = "n0-null"
    version = "1"

    async def generate(self, case: EvalCase, llm: LLM, rep: int) -> GenerationResult:
        sections = [SectionOut(id=s.id, title=s.title, level=1, blocks=data_blocks_for(case, s.id)) for s in case.sections]
        result = GenerationResult(
            generator=self.name,
            generator_version=self.version,
            case_id=case.id,
            rep=rep,
            title=case.title,
            sections=sections,
            calls=[],
            wall_ms=0,
        )
        return finalize(result)
