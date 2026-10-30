"""Load evals/config.toml."""

from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

EVALS_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = EVALS_DIR / "config.toml"


@lru_cache(maxsize=None)
def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    with open(path, "rb") as f:
        return tomllib.load(f)


def price_for(model: str, config: dict[str, Any] | None = None) -> dict[str, float] | None:
    """USD per million tokens for ``model``, or None when the model is unpriced."""
    cfg = config if config is not None else load_config()
    return cfg.get("prices", {}).get(model)


def cost_usd(
    model: str,
    *,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    config: dict[str, Any] | None = None,
) -> float | None:
    """Cost of one call. ``input_tokens`` excludes cache reads and writes, as the API reports it."""
    p = price_for(model, config)
    if p is None:
        return None
    return (
        input_tokens * p["input"]
        + output_tokens * p["output"]
        + cache_read_tokens * p["cache_read"]
        + cache_write_tokens * p["cache_write"]
    ) / 1_000_000
