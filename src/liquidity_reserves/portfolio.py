"""Regional pools with overlapping shared shocks.

Countries form a chain. Each adjacent pair (i, i+1) has its own shared
earthquake source and shared precipitation source. A shared earthquake
triggers both neighbours with independent severity marks; a shared
precipitation event triggers both neighbours' single payment. Each country's
private rate is its calibrated marginal rate minus the shared rates of its
incident pairs, so marginal frequencies stay fixed as shared rates vary.

The analyst's range is a rectangle over two scalars applied to every pair:
an earthquake multiplier of the base shared earthquake rate, and a rain share
of the smaller adjacent marginal precipitation rate.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .money import UNITS_PER_CENT, format_usd

OUTCOMES = 10  # (earthquake quarter steps 0..4) x (precipitation paid 0/1); index 2k + r
SHARED_STATES = 10  # (shared earthquake count 0..3, >=4) x (shared rain event 0/1); index 2e + r
MAX_COUNTRIES = 60
# Relative slack for private rates that are zero up to floating-point error,
# e.g. when a shared rate is set exactly equal to a marginal rate.
_RATE_SLACK = 1e-12


class InputError(ValueError):
    """Invalid analyst input. `messages` lists every problem found."""

    def __init__(self, messages):
        if isinstance(messages, str):
            messages = [messages]
        self.messages = list(messages)
        super().__init__("; ".join(self.messages))


def _finite_nonnegative(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float, np.floating, np.integer)):
        raise InputError(f"{name} must be a number")
    value = float(value)
    if not math.isfinite(value):
        raise InputError(f"{name} must be finite")
    if value < 0:
        raise InputError(f"{name} cannot be negative")
    return value


@dataclass(frozen=True)
class Country:
    code: str
    name: str
    earthquake_rate: float  # annual marginal rate of earthquake trigger events
    severity: tuple  # P(one trigger pays 1, 2, 3 or 4 quarter steps)
    rain_rate: float  # annual marginal rate of precipitation trigger events
    earthquake_cap_units: int  # quarter-cents; earthquake payments accumulate to this cap
    rain_cap_units: int  # quarter-cents; precipitation pays once over the term

    def __post_init__(self):
        if not isinstance(self.code, str) or not self.code.strip():
            raise InputError("each country needs a code")
        label = self.code
        _finite_nonnegative(self.earthquake_rate, f"{label} earthquake rate")
        _finite_nonnegative(self.rain_rate, f"{label} precipitation rate")
        severity = np.asarray(self.severity, dtype=float)
        if severity.shape != (4,) or not np.all(np.isfinite(severity)) or np.any(severity < 0):
            raise InputError(f"{label} severity mix must be four nonnegative probabilities")
        if abs(severity.sum() - 1) > 1e-9:
            raise InputError(f"{label} severity mix must sum to 1")
        for name, cap in (
            ("earthquake cap", self.earthquake_cap_units),
            ("precipitation payment", self.rain_cap_units),
        ):
            if isinstance(cap, bool) or not isinstance(cap, (int, np.integer)) or cap < 0:
                raise InputError(f"{label} {name} must be a nonnegative whole number of cents")
            if cap % UNITS_PER_CENT:
                raise InputError(f"{label} {name} must be a whole number of cents")

    @property
    def principal_units(self) -> int:
        return int(self.earthquake_cap_units + self.rain_cap_units)

    def payouts(self) -> np.ndarray:
        """Exact payout in quarter-cents for each of the 10 outcomes."""
        steps = np.arange(5, dtype=np.int64).repeat(2)
        rain = np.tile(np.array([0, 1], dtype=np.int64), 5)
        quarter = np.int64(self.earthquake_cap_units // 4)
        return steps * quarter + rain * np.int64(self.rain_cap_units)

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "name": self.name,
            "earthquake_rate_per_year": self.earthquake_rate,
            "severity_mix": [float(x) for x in self.severity],
            "rain_rate_per_year": self.rain_rate,
            "earthquake_cap_usd": self.earthquake_cap_units / 400,
            "rain_payment_usd": self.rain_cap_units / 400,
            "earthquake_cap": format_usd(self.earthquake_cap_units),
            "rain_payment": format_usd(self.rain_cap_units),
        }


@dataclass(frozen=True)
class Rates:
    """Annual Poisson rates of every source at one assumption (or a dominating model)."""

    earthquake_private: np.ndarray
    earthquake_shared: np.ndarray
    rain_private: np.ndarray
    rain_shared: np.ndarray

    def to_dict(self) -> dict:
        return {
            "earthquake_private_per_year": self.earthquake_private.tolist(),
            "earthquake_shared_per_year": self.earthquake_shared.tolist(),
            "rain_private_per_year": self.rain_private.tolist(),
            "rain_shared_per_year": self.rain_shared.tolist(),
        }


@dataclass(frozen=True)
class SharedShockRange:
    """Rectangle of shared-shock assumptions applied to every adjacent pair."""

    earthquake_multiplier: tuple  # (low, high) multiples of the base shared earthquake rate
    rain_share: tuple  # (low, high) shares of the smaller adjacent precipitation rate

    def __post_init__(self):
        for name, pair in (
            ("shared earthquake multiplier", self.earthquake_multiplier),
            ("shared rain share", self.rain_share),
        ):
            if len(pair) != 2:
                raise InputError(f"{name} range needs a low and a high value")
            low = _finite_nonnegative(pair[0], f"{name} low")
            high = _finite_nonnegative(pair[1], f"{name} high")
            if low > high:
                raise InputError(f"{name} low value exceeds its high value")

    @property
    def low(self) -> np.ndarray:
        return np.array([self.earthquake_multiplier[0], self.rain_share[0]], dtype=float)

    @property
    def high(self) -> np.ndarray:
        return np.array([self.earthquake_multiplier[1], self.rain_share[1]], dtype=float)

    def contains(self, point) -> bool:
        point = np.asarray(point, dtype=float)
        return bool(np.all(point >= self.low) and np.all(point <= self.high))

    def corners(self) -> list:
        """Distinct corners, ordered (low, low), (low, high), (high, low), (high, high)."""
        seen, corners = set(), []
        for m in (self.earthquake_multiplier[0], self.earthquake_multiplier[1]):
            for s in (self.rain_share[0], self.rain_share[1]):
                key = (float(m), float(s))
                if key not in seen:
                    seen.add(key)
                    corners.append(key)
        return corners

    def to_dict(self) -> dict:
        return {
            "earthquake_multiplier": [float(x) for x in self.earthquake_multiplier],
            "rain_share": [float(x) for x in self.rain_share],
        }


@dataclass(frozen=True)
class RegionalPool:
    name: str
    countries: tuple
    term_years: float
    base_shared_earthquake: tuple  # base shared earthquake rate for each adjacent pair
    base_point: tuple  # (earthquake multiplier, rain share) of the base assumptions
    synthetic: bool = False
    description: str = ""
    source: dict = field(default_factory=dict)

    def __post_init__(self):
        n = len(self.countries)
        if not 1 <= n <= MAX_COUNTRIES:
            raise InputError(f"a pool needs 1 to {MAX_COUNTRIES} countries")
        codes = [c.code for c in self.countries]
        if len(set(codes)) != n:
            raise InputError("country codes must be distinct")
        term = _finite_nonnegative(self.term_years, "term (years)")
        if not 0 < term <= 100:
            raise InputError("term must be more than 0 and at most 100 years")
        if len(self.base_shared_earthquake) != n - 1:
            raise InputError("one base shared earthquake rate is needed per adjacent pair")
        for i, rate in enumerate(self.base_shared_earthquake):
            _finite_nonnegative(rate, f"base shared earthquake rate {codes[i]}-{codes[i + 1]}")
        if len(self.base_point) != 2:
            raise InputError("base assumptions need an earthquake multiplier and a rain share")
        _finite_nonnegative(self.base_point[0], "base shared earthquake multiplier")
        _finite_nonnegative(self.base_point[1], "base shared rain share")

    @property
    def n(self) -> int:
        return len(self.countries)

    @property
    def codes(self) -> list:
        return [c.code for c in self.countries]

    @property
    def pair_labels(self) -> list:
        return [f"{a}-{b}" for a, b in zip(self.codes, self.codes[1:], strict=False)]

    @property
    def earthquake_marginal(self) -> np.ndarray:
        return np.array([c.earthquake_rate for c in self.countries], dtype=float)

    @property
    def rain_marginal(self) -> np.ndarray:
        return np.array([c.rain_rate for c in self.countries], dtype=float)

    @property
    def rain_pair_minimum(self) -> np.ndarray:
        rain = self.rain_marginal
        return np.minimum(rain[:-1], rain[1:])

    @property
    def principal_units(self) -> int:
        return sum(c.principal_units for c in self.countries)

    def payout_table(self) -> np.ndarray:
        return np.array([c.payouts() for c in self.countries], dtype=np.int64)

    def shared_rates(self, point):
        m, s = (float(x) for x in point)
        earthquake = m * np.asarray(self.base_shared_earthquake, dtype=float)
        rain = s * self.rain_pair_minimum
        return earthquake, rain

    def rate_axes(self) -> np.ndarray:
        """Total shared rate added per unit of each range coordinate (sums over pairs)."""
        return np.array([sum(self.base_shared_earthquake), self.rain_pair_minimum.sum()])

    @staticmethod
    def _incident(shared: np.ndarray, n: int) -> np.ndarray:
        return np.r_[shared, 0.0] + np.r_[0.0, shared]

    def infeasibilities(self, point) -> list:
        """Messages describing why an assumption violates a country's marginal rates."""
        earthquake, rain = self.shared_rates(point)
        problems = []
        for label, shared, marginal, peril in (
            ("earthquake", earthquake, self.earthquake_marginal, "earthquake"),
            ("rain", rain, self.rain_marginal, "precipitation"),
        ):
            incident = self._incident(shared, self.n)
            for i, country in enumerate(self.countries):
                if incident[i] > marginal[i] * (1 + _RATE_SLACK) + 1e-300:
                    problems.append(
                        f"At shared earthquake multiplier {point[0]:g} and rain share "
                        f"{100 * point[1]:.4g}%, shared {peril} events next to {country.code} "
                        f"occur {incident[i]:.6g} times a year, more than its marginal "
                        f"{label} rate of {marginal[i]:.6g} a year."
                    )
        return problems

    def rates(self, point) -> Rates:
        """Feasible rates at an assumption; marginal rates are preserved exactly."""
        problems = self.infeasibilities(point)
        if problems:
            raise InputError(problems)
        earthquake, rain = self.shared_rates(point)
        return Rates(
            earthquake_private=self._private(self.earthquake_marginal, earthquake),
            earthquake_shared=earthquake,
            rain_private=self._private(self.rain_marginal, rain),
            rain_shared=rain,
        )

    def dominating_rates(self, low, high) -> Rates:
        """Rates that dominate every assumption in the cell [low, high].

        Shared rates are taken at the cell's high corner and private rates at
        its low corner. Every feasible model in the cell is a thinning of this
        one, and capped payouts never fall when events are added, so its
        exceedance probabilities bound those of the whole cell. It does not
        preserve marginal rates and is not itself a feasible scenario.
        """
        problems = self.infeasibilities(high)
        if problems:
            raise InputError(problems)
        earthquake_low, rain_low = self.shared_rates(low)
        earthquake_high, rain_high = self.shared_rates(high)
        return Rates(
            earthquake_private=self._private(self.earthquake_marginal, earthquake_low),
            earthquake_shared=earthquake_high,
            rain_private=self._private(self.rain_marginal, rain_low),
            rain_shared=rain_high,
        )

    def _private(self, marginal, shared) -> np.ndarray:
        private = marginal - np.r_[shared, 0.0] - np.r_[0.0, shared]
        tiny = np.abs(private) <= _RATE_SLACK * np.maximum(marginal, 1e-300)
        private = np.where(tiny & (private < 0), 0.0, private)
        if np.any(private < 0):
            raise InputError("shared rates exceed a country's marginal rate")
        return private

    def describe_point(self, point) -> dict:
        earthquake, rain = self.shared_rates(point)
        return {
            "earthquake_multiplier": float(point[0]),
            "rain_share": float(point[1]),
            "pairs": [
                {
                    "pair": label,
                    "shared_earthquake_per_year": float(e),
                    "shared_rain_per_year": float(r),
                }
                for label, e, r in zip(self.pair_labels, earthquake, rain, strict=True)
            ],
        }

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "synthetic": self.synthetic,
            "description": self.description,
            "term_years": self.term_years,
            "countries": [c.to_dict() for c in self.countries],
            "base_shared_earthquake_per_year": [float(x) for x in self.base_shared_earthquake],
            "base_point": {
                "earthquake_multiplier": float(self.base_point[0]),
                "rain_share": float(self.base_point[1]),
            },
            "source": self.source,
        }
