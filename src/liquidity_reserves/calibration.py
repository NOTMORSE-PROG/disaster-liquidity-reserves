"""Calibration of public ADB bond inputs to annual Poisson rates.

This reproduces the organizer's quarter-step model so the engine can build
the public example without depending on the reference solver. A parity test
compares it with qjlb.bond.earthquake_jump_rates.
"""

from __future__ import annotations

import numpy as np


def earthquake_jump_rates(tail_probabilities) -> np.ndarray:
    """Annual rates of 1..4 quarter-step earthquake payouts from annual tail probabilities.

    tail_probabilities[j] is P(annual payout fraction >= (j + 1) / 4).
    """
    tail = np.asarray(tail_probabilities, dtype=float)
    if tail.shape != (4,) or not np.all(np.isfinite(tail)):
        raise ValueError("earthquake tails must contain four finite probabilities")
    if np.any(tail <= 0) or np.any(tail >= 1) or np.any(np.diff(tail) > 0):
        raise ValueError("earthquake tails must decrease within (0, 1)")
    p0, p1, p2, p3, _ = -np.diff(np.r_[1.0, tail, 0.0])
    total = -np.log(p0)
    r1 = p1 / p0
    r2 = p2 / p0 - r1**2 / 2
    r3 = p3 / p0 - r1 * r2 - r1**3 / 6
    rates = np.array([r1, r2, r3, total - r1 - r2 - r3])
    if np.any(rates < 0):
        raise ValueError("annual tails do not support nonnegative Poisson jump rates")
    return rates


def precipitation_rate(annual_probability: float, multiplier: float = 1.0) -> float:
    """Annual trigger rate from an annual trigger probability."""
    p = float(annual_probability)
    if not 0 < p < 1:
        raise ValueError("precipitation probability must be in (0, 1)")
    if not np.isfinite(multiplier) or multiplier <= 0:
        raise ValueError("precipitation rate multiplier must be positive")
    return float(-np.log1p(-p) * multiplier)


def shared_earthquake_rate(totals, conditionals: dict, countries) -> float:
    """Base shared-event rate for a country pair.

    The rounded public same-event conditionals imply slightly different shared
    rates; the organizer averages the two, and so does this function.
    """
    a, b = countries
    return float(
        np.mean(
            [
                totals[0] * conditionals[f"{b}_given_{a}"],
                totals[1] * conditionals[f"{a}_given_{b}"],
            ]
        )
    )
