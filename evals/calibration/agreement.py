"""Agreement statistics for judge calibration (EVAL-13, EVALS_PROPOSAL §6.3).

Every statistic is a function of a confusion matrix ``m`` (rows: the reference rater, e.g.
the human majority; columns: the other rater, e.g. the judge), so a cluster bootstrap only
has to add up per-cluster matrices: resample clusters (cases) with replacement, sum their
matrices, recompute. Statistics return ``None`` when they are undefined (no items, or a
kappa whose chance agreement is 1).

Pairwise verdicts use the categories ``("x", "y", "tie")``: the configured pair's first
generator, its second, or a tie. Absolute scores use ``(1, 2, 3, 4, 5)``.
"""

from __future__ import annotations

import math
import random
from typing import Any, Callable, Hashable, Iterable, Sequence

Matrix = list[list[int]]
Stat = Callable[[Matrix], "float | None"]

PAIR_CATEGORIES: tuple[str, ...] = ("x", "y", "tie")
SCORE_CATEGORIES: tuple[int, ...] = (1, 2, 3, 4, 5)
BOOTSTRAP_RESAMPLES = 10_000
BOOTSTRAP_SEED = 20261030


def confusion(pairs: Iterable[tuple[Hashable, Hashable]], categories: Sequence[Hashable]) -> Matrix:
    """Counts of (reference, other) pairs. Raises ValueError for a value outside ``categories``."""
    index = {c: i for i, c in enumerate(categories)}
    m = [[0] * len(categories) for _ in categories]
    for a, b in pairs:
        if a not in index or b not in index:
            raise ValueError(f"value outside {list(categories)}: {(a, b)!r}")
        m[index[a]][index[b]] += 1
    return m


def add(a: Matrix, b: Matrix) -> Matrix:
    return [[x + y for x, y in zip(ra, rb)] for ra, rb in zip(a, b)]


def total(m: Matrix) -> int:
    return sum(sum(r) for r in m)


def raw_agreement(m: Matrix) -> float | None:
    """Share of items on the diagonal."""
    n = total(m)
    return sum(m[i][i] for i in range(len(m))) / n if n else None


def within_one(m: Matrix) -> float | None:
    """Share of items where the two ratings differ by at most one category (ordinal scales)."""
    n = total(m)
    return sum(m[i][j] for i in range(len(m)) for j in range(len(m)) if abs(i - j) <= 1) / n if n else None


def cohen_kappa(m: Matrix) -> float | None:
    """Unweighted Cohen's kappa: (p_o - p_e) / (1 - p_e)."""
    return weighted_kappa(m, weights="none")


def weighted_kappa(m: Matrix, weights: str = "quadratic") -> float | None:
    """Weighted kappa: 1 - sum(w * observed) / sum(w * expected).

    ``weights``: "quadratic" ((i - j)^2 / (k - 1)^2), "linear" (|i - j| / (k - 1)) or "none"
    (0 on the diagonal, 1 elsewhere: Cohen's kappa). Expected counts come from the two
    raters' marginals. None when there are no items or the expected disagreement is 0
    (both raters used a single, identical category).
    """
    k = len(m)
    n = total(m)
    if not n or k < 2:
        return None

    def w(i: int, j: int) -> float:
        if weights == "quadratic":
            return (i - j) ** 2 / (k - 1) ** 2
        if weights == "linear":
            return abs(i - j) / (k - 1)
        if weights == "none":
            return 0.0 if i == j else 1.0
        raise ValueError(f"unknown weights {weights!r}")

    rows = [sum(r) for r in m]
    cols = [sum(m[i][j] for i in range(k)) for j in range(k)]
    observed = sum(w(i, j) * m[i][j] for i in range(k) for j in range(k)) / n
    expected = sum(w(i, j) * rows[i] * cols[j] for i in range(k) for j in range(k)) / (n * n)
    if expected == 0:
        return None
    return 1.0 - observed / expected


def decided_agreement(m: Matrix) -> float | None:
    """Pairwise matrices only: agreement on items where the reference (row) chose a side.

    Rows and columns are ``PAIR_CATEGORIES`` (x, y, tie). Denominator: items whose reference
    verdict is x or y; numerator: those where the other rater gave the same side. A tie by
    the other rater on such an item counts as a disagreement (EVALS_PROPOSAL §6.3: agreement
    with the human majority on non-tie items).
    """
    den = sum(m[0]) + sum(m[1])
    return (m[0][0] + m[1][1]) / den if den else None


def decided_count(m: Matrix) -> int:
    """Pairwise matrices only: items whose reference (row) verdict is x or y."""
    return sum(m[0]) + sum(m[1])


def both_decided_agreement(m: Matrix) -> float | None:
    """Pairwise matrices only: agreement on items where both raters chose a side (ties dropped)."""
    den = m[0][0] + m[0][1] + m[1][0] + m[1][1]
    return (m[0][0] + m[1][1]) / den if den else None


def mean_difference(m: Matrix, categories: Sequence[float] = SCORE_CATEGORIES) -> float | None:
    """Mean of (column rating - row rating): positive when the column rater scores higher."""
    n = total(m)
    if not n:
        return None
    return sum((categories[j] - categories[i]) * m[i][j] for i in range(len(m)) for j in range(len(m))) / n


# ---------------------------------------------------------------- bootstrap


def bootstrap_ci(
    clusters: Sequence[Matrix],
    stat: Stat,
    *,
    resamples: int = BOOTSTRAP_RESAMPLES,
    seed: int = BOOTSTRAP_SEED,
    alpha: float = 0.05,
) -> tuple[float, float] | None:
    """Percentile CI of ``stat`` over a cluster bootstrap: resample clusters with replacement.

    ``clusters`` holds one confusion matrix per cluster (one per case). Resamples where the
    statistic is undefined are dropped. None when there are no clusters or no defined resample.
    """
    if not clusters:
        return None
    rng = random.Random(seed)
    k = len(clusters)
    size = len(clusters[0])
    stats = []
    for _ in range(resamples):
        m = [[0] * size for _ in range(size)]
        for _ in range(k):
            m = add(m, clusters[rng.randrange(k)])
        v = stat(m)
        if v is not None:
            stats.append(v)
    if not stats:
        return None
    stats.sort()
    lo = stats[int(math.floor(alpha / 2 * (len(stats) - 1)))]
    hi = stats[int(math.ceil((1 - alpha / 2) * (len(stats) - 1)))]
    return lo, hi


def pooled(clusters: dict[str, Matrix], size: int) -> Matrix:
    """The sum of every cluster's matrix (a ``size`` x ``size`` zero matrix when there are none)."""
    m = [[0] * size for _ in range(size)]
    for mat in clusters.values():
        m = add(m, mat)
    return m


def estimate(clusters: dict[str, Matrix], size: int, stat: Stat, **bootstrap: Any) -> dict[str, Any]:
    """Point estimate on the pooled matrix plus its cluster-bootstrap 95% CI (clusters = cases)."""
    value = stat(pooled(clusters, size))
    mats = [clusters[c] for c in sorted(clusters)]
    ci = bootstrap_ci(mats, stat, **bootstrap) if value is not None else None
    return {"value": value, "ci": list(ci) if ci else None}
