"""P3 prototype: P1 + sequential flow (FR-COH-3; EVALS_PROPOSAL §4.3). Approximates ``narrative`` without the editor.

Sections run one at a time in document order. Each call's shared prefix is the frame,
the data and attachments, then "the document so far": a header block and one block per
earlier section (data blocks as Markdown, then the model text), so each prefix extends
the previous one by appending and carries a second cache breakpoint at its end. Above
60,000 characters the oldest sections are cut to their first paragraph. ``depends_on``
text is not repeated in the per-prompt part, since every earlier section is already in
the prefix. Calls: one per section, all on the critical path.
"""

from __future__ import annotations

import time

from evals.core.types import LLM, EvalCase, GenerationResult
from evals.generators import coherence as co
from evals.generators import common as c
from evals.generators.p1_frame import finish


class FlowSequential:
    name = "p3-flow"
    logic_version = "1"

    def __init__(self, max_flow_chars: int = co.MAX_FLOW_CHARS) -> None:
        self.max_flow_chars = max_flow_chars

    @property
    def version(self) -> str:
        return c.generator_version(self.logic_version, co.prompt(co.FRAME), co.prompt(co.FLOW), c.shared())

    def planned_calls(self, case: EvalCase, visible_chars_per_section: int) -> list[int]:
        base = co.shared_chars(case)
        header = len(co.prompt(co.FLOW).render("header"))
        out, so_far = [], 0
        for s in case.sections:
            out.append(base + (header + so_far if so_far else 0) + len(co.flow_section_text(case, s)))
            so_far = min(self.max_flow_chars, so_far + visible_chars_per_section + 80)
        return out

    async def generate(self, case: EvalCase, llm: LLM, rep: int) -> GenerationResult:
        t0 = time.perf_counter()
        draft = await co.write_sequential(case, llm, max_chars=self.max_flow_chars)
        warnings = draft.warnings()
        sections = [c.section_out(case, s, draft.texts[s.id], warnings) for s in case.sections]
        return finish(self, case, rep, sections, llm, t0, warnings)
