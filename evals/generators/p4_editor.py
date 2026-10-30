"""P4 prototype: P2 + the editor pass (FR-COH-4; EVALS_PROPOSAL §4.3). Approximates the ``report`` preset.

After P2's brief and sections, the repetition finder (FR-COH-5, word 8-grams, threshold
2) runs over the draft, and one ``_editor`` call sees the whole draft with section IDs
marked and data blocks marked read-only, plus the findings, behind the same shared
prefix as the sections (frame, data, attachments, brief). It replies with JSON
find/replace edits; malformed JSON gets one retry. An edit is applied only if ``find``
matches exactly once in that section's model-written text, the section's change ratio
stays within ``max_change`` (0.3), and its checks still pass; otherwise it is rejected
or reverted with a warning. A refused or unusable editor reply leaves the draft
unedited. Repetition is checked again after editing. Calls: 1 brief + one per section
+ 1 editor (2 if the JSON needs its retry).
"""

from __future__ import annotations

import time

from evals.core.types import LLM, EvalCase, GenerationResult
from evals.generators import coherence as co
from evals.generators import common as c
from evals.generators.p1_frame import finish
from evals.generators.p2_brief import BriefParallel


class BriefEditor:
    name = "p4-editor"
    logic_version = "1"

    def __init__(self, max_change: float = co.MAX_CHANGE) -> None:
        self.max_change = max_change

    @property
    def version(self) -> str:
        return c.generator_version(
            self.logic_version, co.prompt(co.FRAME), co.prompt(co.BRIEF), co.prompt(co.EDITOR), c.shared()
        )

    def planned_calls(self, case: EvalCase, visible_chars_per_section: int) -> list[int]:
        editor = (co.shared_chars(case, brief_chars=co.BRIEF_CHARS_ESTIMATE) + len(co.prompt(co.EDITOR).parts["user"])
                  + co.draft_chars(case, visible_chars_per_section))
        return BriefParallel().planned_calls(case, visible_chars_per_section) + [editor]

    async def generate(self, case: EvalCase, llm: LLM, rep: int) -> GenerationResult:
        t0 = time.perf_counter()
        warnings: list[str] = []
        brief = await co.write_brief(case, llm, warnings)
        draft = await co.write_parallel(case, llm, brief=brief)
        warnings += draft.warnings()
        sections = [c.section_out(case, s, draft.texts[s.id], warnings) for s in case.sections]
        findings = co.find_repetition(co.model_texts(sections))
        warnings += [f"before editing: {f.warning()}" for f in findings]
        _, edit_warnings = await co.run_editor(case, llm, sections, brief=brief, findings=findings,
                                               max_change=self.max_change)
        warnings += edit_warnings
        return finish(self, case, rep, sections, llm, t0, warnings)  # repetition again, after editing
