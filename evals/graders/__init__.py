"""Deterministic graders (EVAL-8). None of them imports ``doccode``.

``ALL_GRADERS`` holds one instance of each; each implements ``core.types.Grader``:
``grade(case, result) -> list[Score]``, returning [] for a result with ``error`` set.
"""

from __future__ import annotations

from evals.core.types import Grader
from evals.graders.adherence import AdherenceGrader
from evals.graders.chatter import ChatterGrader
from evals.graders.cost import CostGrader
from evals.graders.data_blocks import DataBlocksGrader
from evals.graders.numbers import NumbersGrader
from evals.graders.repetition import RepetitionGrader
from evals.graders.structure import StructureGrader

ALL_GRADERS: list[Grader] = [
    NumbersGrader(),
    RepetitionGrader(),
    StructureGrader(),
    ChatterGrader(),
    AdherenceGrader(),
    DataBlocksGrader(),
    CostGrader(),
]

__all__ = [
    "ALL_GRADERS",
    "AdherenceGrader",
    "ChatterGrader",
    "CostGrader",
    "DataBlocksGrader",
    "NumbersGrader",
    "RepetitionGrader",
    "StructureGrader",
]
