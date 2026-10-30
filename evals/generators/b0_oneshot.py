"""B0 one-shot: one call writes the whole document from the full spec and data (EVALS_PROPOSAL §4.3)."""

from __future__ import annotations

import time

from evals.core.types import LLM, EvalCase, GenerationResult
from evals.generators import common as c

PROMPT = "b0_oneshot"


class OneShot:
    name = "b0-oneshot"
    logic_version = "1"

    @property
    def version(self) -> str:
        return c.generator_version(self.logic_version, c.load_prompt(PROMPT), c.shared())

    def user_text(self, case: EvalCase) -> str:
        return c.load_prompt(PROMPT).render(
            "user",
            document=c.document_text(case),
            outline=c.outline_text(case),
            first_title=case.sections[0].title,
            fact_rules=c.fact_rules(),
        )

    def planned_calls(self, case: EvalCase, visible_chars_per_section: int) -> list[int]:
        """Input characters per call, for the cost projection."""
        return [len(c.system_prompt()) + len(c.context_text(case)) + len(self.user_text(case))]

    async def generate(self, case: EvalCase, llm: LLM, rep: int) -> GenerationResult:
        t0 = time.perf_counter()
        resp = await llm.complete(
            role="generate",
            node_id="document",
            system=c.system_prompt(),
            messages=[c.user_message(case, self.user_text(case))],
        )
        sections, warnings = c.split_document(case, resp.text)
        if resp.call.stop_reason == "max_tokens":
            warnings.append("document: stopped at max_tokens")
        return c.assemble(self, case, rep, sections, llm, t0, warnings)
