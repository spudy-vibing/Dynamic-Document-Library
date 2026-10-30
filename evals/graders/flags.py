"""README flag fidelity: invented or removed CLI flags presented as options (F3; §5.2).

"An invented flag or the wrong licence is a fidelity failure" (EVALS_PROPOSAL §5.2). This
grader covers the flag half deterministically; the licence and the other facts stay with
the claim checker.

Applies to cases whose truth facts have ``cli_flags`` (the tool's real flags, as written
in the ``--help`` text). Optional ``removed_flags`` (comma-separated) lists flags a
``CHANGELOG.md`` attachment says were removed: the seeded trap.

Every ``--long-flag`` in model text is checked, except on lines that run another tool
(``pip``, ``pipx``, ``uv``, ``python -m``, ``git``):

  * a flag that is neither real nor removed is **invented**;
  * a removed flag is **presented as current** unless its sentence says it was removed,
    deprecated, dropped, replaced or no longer exists.

Metric: ``trap.readme_flags``, num = 1 if any invented or presented-as-current flag, den = 1.
Heuristic (``detail.heuristic = true``).
"""

from __future__ import annotations

import re
from typing import Any

from evals.core.types import EvalCase, GenerationResult, Score
from evals.graders.numbers import sentence_spans

_FLAG = re.compile(r"(?<![\w-])--[a-z][a-z0-9]*(?:-[a-z0-9]+)*")
_OTHER_TOOL = re.compile(r"(?i)\b(?:pip3?|pipx|uv|python3? -m|git|brew|npm)\b")
_REMOVAL = re.compile(r"(?i)\b(?:remov\w*|deprecat\w*|dropped|replac\w*|no longer|renamed|retired|instead)\b")


def real_flags(facts: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    for part in str(facts.get("cli_flags", "")).split(","):
        for f in part.strip().split("/"):
            if f.startswith("--"):
                out.add(f.strip())
    return out


class FlagsGrader:
    name = "flags"
    version = "1"

    def grade(self, case: EvalCase, result: GenerationResult) -> list[Score]:
        facts = case.truth.get("facts", {})
        if result.error or "cli_flags" not in facts:
            return []
        real = real_flags(facts)
        removed = {f.strip() for f in str(facts.get("removed_flags", "")).split(",") if f.strip()}
        invented: list[dict[str, str]] = []
        current: list[dict[str, str]] = []
        for s in result.sections:
            text = s.model_text()
            spans = sentence_spans(text)
            line_start = 0
            for line in text.splitlines(keepends=True):
                if not _OTHER_TOOL.search(line):
                    for m in _FLAG.finditer(line):
                        flag, pos = m.group(0), line_start + m.start()
                        if flag in real:
                            continue
                        sentence = next((text[a:b] for a, b in spans if a <= pos < b), line)
                        if flag in removed:
                            if not _REMOVAL.search(sentence):
                                current.append({"section": s.id, "flag": flag, "sentence": sentence.strip()})
                        else:
                            invented.append({"section": s.id, "flag": flag, "sentence": sentence.strip()})
                line_start += len(line)
        hit = 1.0 if invented or current else 0.0
        return [Score("trap.readme_flags", hit, hit, 1.0,
                      {"heuristic": True, "invented": invented, "removed_presented_as_current": current})]
