"""B2 sequential chat chain: one append-only conversation, one user turn per section (EVALS_PROPOSAL §4.3).

The first turn carries the full context (data and attachments, purpose, audience,
style, outline) and asks for section 1; each later turn asks for the next section.
The model's text replies are appended as assistant turns, so every request extends
the previous one.
"""

from __future__ import annotations

import time
from typing import Any

from evals.core.types import LLM, EvalCase, GenerationResult
from evals.generators import common as c

PROMPT = "b2_chain"
EMPTY_REPLY = "[no text returned]"  # the API rejects empty assistant turns


class ChatChain:
    name = "b2-chain"
    logic_version = "1"

    @property
    def version(self) -> str:
        return c.generator_version(self.logic_version, c.load_prompt(PROMPT), c.shared())

    def turn_text(self, case: EvalCase, index: int) -> str:
        p = c.load_prompt(PROMPT)
        s = case.sections[index]
        if index == 0:
            return p.render(
                "first_turn",
                document=c.document_text(case),
                outline=c.outline_text(case),
                fact_rules=c.fact_rules(),
                count=len(case.sections),
                title=s.title,
            )
        return p.render("next_turn", index=index + 1, count=len(case.sections), title=s.title)

    def planned_calls(self, case: EvalCase, visible_chars_per_section: int) -> list[int]:
        total = len(c.system_prompt()) + len(c.context_text(case))
        out = []
        for i in range(len(case.sections)):
            total += len(self.turn_text(case, i))
            out.append(total)
            total += visible_chars_per_section
        return out

    async def generate(self, case: EvalCase, llm: LLM, rep: int) -> GenerationResult:
        t0 = time.perf_counter()
        messages: list[dict[str, Any]] = []
        warnings: list[str] = []
        texts: dict[str, str] = {}
        for i, s in enumerate(case.sections):
            text = self.turn_text(case, i)
            messages.append(c.user_message(case, text) if i == 0 else {"role": "user", "content": text})
            resp = await llm.complete(role="generate", node_id=s.id, system=c.system_prompt(), messages=list(messages))
            if resp.call.stop_reason == "max_tokens":
                warnings.append(f"section {s.id}: stopped at max_tokens")
            texts[s.id] = resp.text
            messages.append({"role": "assistant", "content": resp.text if resp.text.strip() else EMPTY_REPLY})
        sections = [c.section_out(case, s, texts[s.id], warnings) for s in case.sections]
        return c.assemble(self, case, rep, sections, llm, t0, warnings)
