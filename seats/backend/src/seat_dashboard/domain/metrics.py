from __future__ import annotations

from math import isfinite


def safe_ratio(numerator: float | int | None, denominator: float | int | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    value = float(numerator) / float(denominator)
    return value if isfinite(value) else None


def wilson_interval(wins: int, samples: int, z: float = 1.96) -> tuple[float | None, float | None]:
    if samples <= 0 or wins < 0 or wins > samples:
        return None, None
    p = wins / samples
    denominator = 1 + z * z / samples
    center = (p + z * z / (2 * samples)) / denominator
    spread = z * ((p * (1 - p) / samples + z * z / (4 * samples * samples)) ** 0.5) / denominator
    return max(0.0, center - spread), min(1.0, center + spread)
