"""Generator registry. Every generator implements core.types.Generator.

``n0-null`` (the null baseline, owned by the graders' work package) is registered when
its module exists, so the registry works before and after it lands.
"""

from __future__ import annotations

from evals.core.types import Generator
from evals.generators.b0_oneshot import OneShot
from evals.generators.b1_parallel import NaiveParallel
from evals.generators.b2_chain import ChatChain

GENERATORS: dict[str, Generator] = {g.name: g for g in (OneShot(), NaiveParallel(), ChatChain())}

try:
    from evals.generators.n0_null import NullBaseline
except ModuleNotFoundError as e:  # pragma: no cover - depends on the other work package
    if e.name != "evals.generators.n0_null":
        raise
    NullBaseline = None  # type: ignore[assignment,misc]
else:
    GENERATORS[NullBaseline.name] = NullBaseline()

#: Generators that make no model calls (no LLM cost, nothing to project).
NO_LLM = frozenset({"n0-null"})

#: The default set for a run: the MVP baselines.
DEFAULT_GENERATORS = ("b0-oneshot", "b1-parallel", "b2-chain")
