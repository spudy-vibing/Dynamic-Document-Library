"""Cost, cache hit rate and wall time from the generation's call records (EVAL-8, §6.1).

  cost_usd   value = sum of call.cost_usd over the result's calls; None if any call is unpriced
             (cost None); 0.0 for a document made with no calls
  cache_hit  num = sum cache_read_tokens, den = sum (cache_read + cache_write + input) tokens,
             pooled over calls (input_tokens excludes cache reads and writes, as the API reports)
  wall_ms    value = result.wall_ms
"""

from __future__ import annotations

from evals.core.types import EvalCase, GenerationResult, Score


class CostGrader:
    name = "cost"
    version = "1"

    def grade(self, case: EvalCase, result: GenerationResult) -> list[Score]:
        if result.error:
            return []
        calls = result.calls
        costs = [c.cost_usd for c in calls]
        unpriced = [c.requested_model for c in calls if c.cost_usd is None]
        cost = None if unpriced else float(sum(c for c in costs if c is not None))
        read = sum(c.cache_read_tokens for c in calls)
        den = sum(c.cache_read_tokens + c.cache_write_tokens + c.input_tokens for c in calls)
        return [
            Score("cost_usd", cost, detail={"calls": len(calls), "unpriced": sorted(set(unpriced))}),
            Score("cache_hit", read / den if den else None, read, den),
            Score("wall_ms", float(result.wall_ms)),
        ]
