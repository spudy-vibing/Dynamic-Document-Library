"""Output checks (FR-OUT-4). Spike: real logic, but a failure only becomes a warning (no retry)."""

from __future__ import annotations

import json
import re

from ._core import Node

_NUM = re.compile(r"[$€£]?\d[\d,]*(?:\.\d+)?(?:\s*%|[kKmMbB]\b)?")


def _numbers(text: str) -> list[float]:
    out = []
    for m in _NUM.finditer(text):
        raw = m.group(0).strip()
        mult = {"k": 1e3, "m": 1e6, "b": 1e9}.get(raw[-1].lower(), 1)
        digits = re.sub(r"[^\d.]", "", raw).rstrip(".")
        try:
            out.append(float(digits) * mult)
        except ValueError:
            continue
    return out


class _Check:
    name = "check"

    def sources(self) -> tuple:
        return ()

    def fingerprint(self) -> str:
        return self.name

    def __repr__(self) -> str:
        return f"checks.{self.name}"


class max_words(_Check):
    def __init__(self, n: int):
        self.n, self.name = n, f"max_words({n})"

    def __call__(self, text, sources):
        w = len(text.split())
        return f"{w} words > {self.n}" if w > self.n else None


class min_words(_Check):
    def __init__(self, n: int):
        self.n, self.name = n, f"min_words({n})"

    def __call__(self, text, sources):
        w = len(text.split())
        return f"{w} words < {self.n}" if w < self.n else None


class numbers_from(_Check):
    name = "numbers_from"

    def __init__(self, *sources):
        self._all = sources

    def sources(self):
        return tuple(s for s in self._all if isinstance(s, Node))

    def __call__(self, text, resolved):
        pool_text = " ".join(json.dumps(s, default=str) for s in self._all
                             if not isinstance(s, Node))
        pool_text += " " + " ".join(str(r) for r in resolved)
        pool = _numbers(pool_text)
        bad = []
        for v in _numbers(text):
            if v == int(v) and (1900 <= v <= 2100 or 0 <= v <= 10):
                continue
            if not any(abs(v - p) <= 0.005 * max(abs(p), 1e-9) for p in pool):
                bad.append(v)
        return f"unsupported numbers: {bad[:5]}" if bad else None


class custom(_Check):
    def __init__(self, fn, *, name: str, sources=()):
        self.fn, self.name, self._src = fn, name, tuple(sources)

    def sources(self):
        return self._src

    def __call__(self, text, sources):
        return self.fn(text, sources)
