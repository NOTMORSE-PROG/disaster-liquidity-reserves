"""Poisson count buckets and capped severity convolutions."""

from __future__ import annotations

import numpy as np
from scipy.special import gammainc

# Every earthquake trigger pays at least one quarter step, so four or more
# events always exhaust the cap. Counts are therefore kept as 0, 1, 2, 3, >=4.
COUNT_BUCKETS = 5
MAX_STEPS = 4


def poisson_buckets(mean) -> np.ndarray:
    """P(N = 0..3) and P(N >= 4) for N ~ Poisson(mean); vectorised over mean."""
    mean = np.asarray(mean, dtype=float)
    if not np.all(np.isfinite(mean)) or np.any(mean < 0):
        raise ValueError("Poisson means must be finite and nonnegative")
    p0 = np.exp(-mean)
    p1 = p0 * mean
    p2 = p1 * mean / 2
    p3 = p2 * mean / 3
    # The regularised lower incomplete gamma P(4, m) equals P(N >= 4) directly,
    # avoiding cancellation in 1 - (p0 + p1 + p2 + p3) for small means.
    return np.stack((p0, p1, p2, p3, gammainc(4, mean)), axis=-1)


def capped_convolve(left: np.ndarray, right: np.ndarray) -> np.ndarray:
    full = np.convolve(left, right)
    return np.r_[full[:MAX_STEPS], full[MAX_STEPS:].sum()]


def severity_powers(severity) -> np.ndarray:
    """Row n is the law of min(sum of n severity marks, 4) over 0..4 quarter steps."""
    mark = np.r_[0.0, np.asarray(severity, dtype=float)]
    rows = [np.eye(COUNT_BUCKETS)[0]]
    for _ in range(MAX_STEPS - 1):
        rows.append(capped_convolve(rows[-1], mark))
    # Four marks of at least one step each always reach the cap; set the row
    # exactly rather than as a floating-point sum that can miss 1 by an ulp.
    rows.append(np.eye(COUNT_BUCKETS)[MAX_STEPS])
    return np.array(rows)
