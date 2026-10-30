"""Generator registry. Every generator implements core.types.Generator.

Baselines B0-B2 (WP-013) and the coherence prototypes P1-P4 (WP-017) are registered here.

``n0-null`` (the null baseline, owned by the graders' work package) is registered when
its module exists, so the registry works before and after it lands.
"""

from __future__ import annotations

from evals.core.types import Generator
from evals.generators.b0_oneshot import OneShot
from evals.generators.b1_parallel import NaiveParallel
from evals.generators.b2_chain import ChatChain
from evals.generators.p1_frame import FrameParallel
from evals.generators.p2_brief import BriefParallel
from evals.generators.p3_flow import FlowSequential
from evals.generators.p4_editor import BriefEditor

GENERATORS: dict[str, Generator] = {
    g.name: g
    for g in (OneShot(), NaiveParallel(), ChatChain(), FrameParallel(), BriefParallel(), FlowSequential(), BriefEditor())
}

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

#: The coherence prototypes (WP-017, EVAL-4). Not in the default set: the G0 ablation
#: selects them with ``--generators``. Each maps to the layer it adds and the generator
#: it adds it to, so the ablation report can show per-layer increments.
PROTOTYPES: dict[str, tuple[str, str]] = {
    "p1-frame": ("frame", "b1-parallel"),
    "p2-brief": ("shared brief", "p1-frame"),
    "p3-flow": ("sequential flow", "p1-frame"),
    "p4-editor": ("editor", "p2-brief"),
}
