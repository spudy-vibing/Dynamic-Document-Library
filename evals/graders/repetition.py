"""Cross-section repetition (XREP) on model-written text (EVAL-8, §6.1).

Content words: lower-cased words with punctuation and stop words removed. ``G(i)`` is
the set of word n-grams over section i's content words, with n = ``grading.ngram`` (6;
deliberately not FR-COH-5's 8). For each pair of model-written sections (i, j):

    overlap(i, j) = |G(i) ∩ G(j)| / min(|G(i)|, |G(j)|)      (0 if either set is empty)

Metrics:
  xrep       value = mean overlap over all pairs (None with fewer than two model sections)
  rep_pairs  num = pairs sharing at least one n-gram, den = pairs
"""

from __future__ import annotations

import re
from itertools import combinations
from typing import Any

from evals.core.types import EvalCase, GenerationResult, Score
from evals.graders.text import grading_config, model_sections

STOP_WORDS = frozenset(
    """
    a about above after again against all also am an and any are as at be because been before being below
    between both but by can could did do does doing down during each either else ever few for from further
    had has have having he her here hers herself him himself his how however i if in into is it its itself
    just let me more most much must my myself no nor not now of off on once only or other ought our ours
    ourselves out over own per same shall she should so some such than that the their theirs them themselves
    then there these they this those through thus to too under until up upon us very was we were what when
    where whether which while who whom whose why will with within without would yet you your yours yourself
    yourselves s t d ll re ve m
    """.split()
)
_TOKEN = re.compile(r"[a-z0-9]+")


def content_words(text: str) -> list[str]:
    return [w for w in _TOKEN.findall(text.lower().replace("’", "'").replace("'", "")) if w not in STOP_WORDS]


def ngrams(words: list[str], n: int) -> set[tuple[str, ...]]:
    return {tuple(words[i : i + n]) for i in range(len(words) - n + 1)}


def overlap(a: set[tuple[str, ...]], b: set[tuple[str, ...]]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


class RepetitionGrader:
    name = "repetition"
    version = "1"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.n = int(grading_config(config)["ngram"])

    def grade(self, case: EvalCase, result: GenerationResult) -> list[Score]:
        if result.error:
            return []
        grams = {s.id: ngrams(content_words(s.model_text()), self.n) for s in model_sections(result)}
        pairs = []
        for (i, gi), (j, gj) in combinations(grams.items(), 2):
            shared = gi & gj
            pairs.append({"a": i, "b": j, "overlap": overlap(gi, gj), "shared": [" ".join(g) for g in sorted(shared)[:5]]})
        n_pairs = len(pairs)
        with_shared = sum(1 for p in pairs if p["shared"])
        xrep = sum(p["overlap"] for p in pairs) / n_pairs if n_pairs else None
        detail = {"n": self.n, "pairs": [p for p in pairs if p["shared"]]}
        return [
            Score("xrep", xrep, detail=detail),
            Score("rep_pairs", with_shared / n_pairs if n_pairs else None, with_shared, n_pairs, {}),
        ]
