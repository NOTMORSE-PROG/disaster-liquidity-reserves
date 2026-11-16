"""Aggregate payout distributions and reserve queries.

Exceedance is strict: a reserve R falls short only when the payout L is
strictly greater than R, so a payout exactly equal to the reserve is covered.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .money import UNITS_PER_USD


@dataclass(frozen=True, eq=False)
class Curve:
    support_units: np.ndarray  # sorted payout amounts in quarter-cents
    probabilities: np.ndarray

    def __post_init__(self):
        support = np.asarray(self.support_units, dtype=np.int64)
        probs = np.asarray(self.probabilities, dtype=float)
        if support.ndim != 1 or support.shape != probs.shape or len(support) == 0:
            raise ValueError("support and probabilities must be matching 1-D arrays")
        if np.any(np.diff(support) <= 0):
            raise ValueError("support must be strictly increasing")
        # Summing the upper tail from the top keeps small exceedance
        # probabilities accurate instead of forming 1 - CDF.
        tail = np.r_[np.cumsum(probs[:0:-1])[::-1], 0.0]
        amounts = support.astype(float) / UNITS_PER_USD
        upper_moment = np.r_[np.cumsum((probs * amounts)[:0:-1])[::-1], 0.0]
        object.__setattr__(self, "support_units", support)
        object.__setattr__(self, "probabilities", probs)
        object.__setattr__(self, "tail", tail)
        object.__setattr__(self, "excess", np.maximum(upper_moment - amounts * tail, 0.0))

    @property
    def total_mass(self) -> float:
        return float(self.probabilities.sum())

    @property
    def mean_usd(self) -> float:
        return float(self.probabilities @ (self.support_units.astype(float) / UNITS_PER_USD))

    def _index(self, amount_units: int) -> int:
        if amount_units < 0:
            raise ValueError("amounts cannot be negative")
        return int(np.searchsorted(self.support_units, amount_units, side="right")) - 1

    def exceedance(self, amount_units: int) -> float:
        """P(L > amount)."""
        j = self._index(amount_units)
        return self.total_mass if j < 0 else float(self.tail[j])

    def expected_excess(self, amount_units: int) -> float:
        """E[max(L - amount, 0)] in USD."""
        j = self._index(amount_units)
        if j < 0:
            return self.mean_usd - amount_units / UNITS_PER_USD
        amount = amount_units / UNITS_PER_USD
        upper = self.excess[j] + (self.support_units[j] / UNITS_PER_USD - amount) * self.tail[j]
        return float(max(upper, 0.0))

    def reserve_index(self, risk_limit: float, shift: float = 0.0) -> int:
        """First support index whose exceedance plus `shift` is at most the risk limit."""
        adjusted = self.tail + shift
        adjusted[-1] = 0.0  # nothing exceeds the largest attainable payout
        return int(np.flatnonzero(adjusted <= risk_limit)[0])

    def reserve_units(self, risk_limit: float, shift: float = 0.0) -> int:
        return int(self.support_units[self.reserve_index(risk_limit, shift)])
