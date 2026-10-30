"""Structure score (EVAL-8, §6.1).

Checks, on ``result.markdown`` (rendered with core.render if empty) and ``result.sections``
against the case outline:

  no_extra_h1       no H1 other than the title line
  no_skipped_levels heading levels never jump by more than one (## → #### fails)
  no_title_repeat   no heading inside a section's model text repeats that section's title
  sections_present  every case section is present and has no error block
  sections_ordered  the present case sections appear in outline order

Metric: structure, value = checks passed / checks (0..1); failing checks are in detail.
Data blocks are scored separately by ``data_blocks``.
"""

from __future__ import annotations

from evals.core.render import render_markdown
from evals.core.types import EvalCase, GenerationResult, Score
from evals.graders.text import headings, norm_title


class StructureGrader:
    name = "structure"
    version = "1"

    def grade(self, case: EvalCase, result: GenerationResult) -> list[Score]:
        if result.error:
            return []
        md = result.markdown or render_markdown(result.title, result.sections)
        hs = headings(md)
        failures: dict[str, list[str]] = {}

        body = hs[1:] if hs and hs[0].level == 1 else hs
        extra = [h.text for h in body if h.level == 1]
        if extra:
            failures["no_extra_h1"] = extra

        skipped = []
        prev = hs[0].level if hs else 1
        for h in hs[1:]:
            if h.level > prev + 1:
                skipped.append(f"h{prev} -> h{h.level} at {h.text!r}")
            prev = h.level
        if skipped:
            failures["no_skipped_levels"] = skipped

        repeats = []
        for s in result.sections:
            title = norm_title(s.title)
            for h in headings(s.model_text()):
                if norm_title(h.text) == title:
                    repeats.append(f"{s.id}: {h.text!r}")
        if repeats:
            failures["no_title_repeat"] = repeats

        by_id = {s.id: s for s in result.sections}
        missing = [
            c.id for c in case.sections if c.id not in by_id or any(b.origin == "error" for b in by_id[c.id].blocks)
        ]
        if missing:
            failures["sections_present"] = missing

        outline = [c.id for c in case.sections]
        seen = [s.id for s in result.sections if s.id in outline]
        if seen != sorted(seen, key=outline.index):
            failures["sections_ordered"] = seen

        checks = 5
        return [Score("structure", (checks - len(failures)) / checks, detail={"failed": failures})]
