"""IR code formats: raw timings (as learned by the deck) and Pronto hex (as found in IR code databases)."""
from __future__ import annotations

import re


def parse_code(code) -> tuple[list[int], int | None]:
    """-> (timings in microseconds: mark, space, mark, ...; carrier kHz if the code says so)"""
    if isinstance(code, (list, tuple)):
        return [abs(int(v)) for v in code], None
    s = str(code or "").strip()
    if not s:
        raise ValueError("empty IR code")
    words = s.split()
    if len(words) >= 6 and all(re.fullmatch(r"[0-9a-fA-F]{4}", w) for w in words):
        return _pronto([int(w, 16) for w in words])
    nums = [abs(int(float(v))) for v in re.findall(r"[-+]?\d+(?:\.\d+)?", s)]
    if len(nums) < 2:
        raise ValueError("an IR code needs at least two timings")
    return nums, None


def _pronto(w: list[int]) -> tuple[list[int], int]:
    if w[0] != 0:
        raise ValueError("only raw Pronto codes (starting with 0000) are supported")
    period_us = w[1] * 0.241246                 # carrier period
    once, rep = w[2], w[3]
    pairs = w[4:4 + 2 * (once + rep)]
    if len(pairs) < 2 * (once + rep):
        raise ValueError("Pronto code is too short")
    # the "once" part, or the repeat part when there is no once part
    timings = [max(1, round(v * period_us)) for v in pairs[:2 * (once or rep)]]
    return timings, round(1000 / period_us)


def format_code(raw: list[int]) -> str:
    return ", ".join(str(int(v)) for v in raw)
