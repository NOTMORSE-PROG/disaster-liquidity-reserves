"""Quantum-inspired tensor-network contraction of the regional payout model.

The pool is a matrix-product (tensor-train) network. Country i contributes a
local tensor A_i[a, b, o]: the probability of payout outcome o given the
incoming shared state a (pair i-1, i) and outgoing shared state b (pair
i, i+1). Each pair contributes a diagonal weight over its shared state. A
shared state records the shared earthquake count (0..3, >=4) and whether a
shared precipitation event occurred, so the bond dimension is 10 and does not
grow with the number of countries.

Contraction runs left to right while carrying a payout register: the message
is indexed by the open bond and the running payout total. Adding a country
shifts the register by that country's payout; a shared state is summed out as
soon as both countries that depend on it have been processed. No country-by-
country joint table is ever formed.

Payout registers:
- exact lattice: every payout is a multiple of the greatest common divisor of
  the payouts, and the register spans that lattice;
- exact sparse: incommensurate amounts (e.g. caps in odd cents) with few
  countries, keeping every distinct exact total;
- rounded: when neither fits, payouts are rounded down for feasible scenarios
  and up for dominating bounds on a coarser lattice, so lower and upper
  reserve bounds remain valid.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import reduce

import numpy as np

from .curve import Curve
from .money import UNITS_PER_CENT, format_usd
from .poisson import COUNT_BUCKETS, poisson_buckets, severity_powers
from .portfolio import OUTCOMES, SHARED_STATES, Rates, RegionalPool

LATTICE_LIMIT = 400_000
SPARSE_LIMIT = 200_000
ROUNDED_TARGET_BINS = 100_000


# ---------------------------------------------------------------------------
# Local tensors


def local_tensors(pool: RegionalPool, rates: Rates):
    """Country tensors A_i (in, out, outcome) and pair weights w_i (shared state)."""
    years = float(pool.term_years)
    n = pool.n
    counts = np.arange(COUNT_BUCKETS)
    private_eq = poisson_buckets(rates.earthquake_private * years)  # (n, 5)
    # Incoming shared + outgoing shared + private earthquakes, capped at four.
    total = np.minimum(counts[:, None, None] + counts[None, :, None] + counts[None, None, :], 4)
    rain_private_none = np.exp(-rates.rain_private * years)
    tensors = []
    for i, country in enumerate(pool.countries):
        powers = severity_powers(country.severity)  # (count, steps)
        quake = np.einsum("p,abpk->abk", private_eq[i], powers[total])  # (in, out, steps)
        rain = np.zeros((2, 2, 2))
        rain[0, 0] = (rain_private_none[i], -np.expm1(-rates.rain_private[i] * years))
        rain[0, 1] = rain[1, 0] = rain[1, 1] = (0.0, 1.0)  # a shared event always triggers
        local = (quake[:, None, :, None, :, None] * rain[None, :, None, :, None, :]).reshape(
            SHARED_STATES, SHARED_STATES, OUTCOMES
        )
        if i == 0:
            local = local[:1]  # no pair to the left: shared state fixed at zero
        if i == n - 1:
            local = local[:, :1]
        tensors.append(np.ascontiguousarray(local))
    weights = []
    for j in range(n - 1):
        quake = poisson_buckets(rates.earthquake_shared[j] * years)
        rain = np.array(
            [np.exp(-rates.rain_shared[j] * years), -np.expm1(-rates.rain_shared[j] * years)]
        )
        weights.append((quake[:, None] * rain[None, :]).ravel())
    return tensors, weights


def tensor_entries(tensors) -> int:
    return int(sum(t.size for t in tensors))


def chain_probability(tensors, weights, masks) -> float:
    """Probability that every country's outcome lies in its mask (no payout register)."""
    vector = np.ones(1)
    for i, local in enumerate(tensors):
        vector = vector @ local[:, :, masks[i]].sum(axis=2)
        if i < len(weights):
            vector = vector * weights[i]
    return float(vector[0])


# ---------------------------------------------------------------------------
# Payout registers


class LatticeRegister:
    """Dense register on a lattice of `step_units`; payouts given in lattice steps."""

    kind = "lattice"

    def __init__(self, pay_steps: np.ndarray, step_units: int):
        self.pay_steps = np.asarray(pay_steps, dtype=np.int64)
        self.step_units = int(step_units)
        self.max_steps = self.pay_steps.max(axis=1)
        size = int(self.max_steps.sum()) + 1
        self.support_units = np.arange(size, dtype=np.int64) * self.step_units

    @property
    def size(self) -> int:
        return len(self.support_units)

    def contract(self, tensors, weights) -> np.ndarray:
        message = np.ones((1, 1))
        for i, local in enumerate(tensors):
            width = message.shape[1]
            # (out state, outcome, register) after summing the incoming bond.
            moved = np.tensordot(local, message, axes=([0], [0]))
            updated = np.zeros((local.shape[1], width + int(self.max_steps[i])))
            for outcome, offset in enumerate(self.pay_steps[i]):
                updated[:, offset : offset + width] += moved[:, outcome, :]
            if i < len(weights):
                updated *= weights[i][:, None]
            message = updated
        return message[0]


class SparseRegister:
    """Register over the distinct exact totals; bookkeeping depends only on payouts."""

    kind = "sparse"

    def __init__(self, pay_units: np.ndarray, limit: int = SPARSE_LIMIT):
        support = np.zeros(1, dtype=np.int64)
        self.scatter = []
        for pay in np.asarray(pay_units, dtype=np.int64):
            sums = support[None, :] + pay[:, None]
            support, inverse = np.unique(sums, return_inverse=True)
            if len(support) > limit:
                raise OverflowError("too many distinct payout totals for an exact register")
            self.scatter.append(inverse.reshape(sums.shape))
        self.support_units = support
        self.step_units = None

    @property
    def size(self) -> int:
        return len(self.support_units)

    def contract(self, tensors, weights) -> np.ndarray:
        message = np.ones((1, 1))
        for i, local in enumerate(tensors):
            moved = np.tensordot(local, message, axes=([0], [0]))
            index = self.scatter[i]
            size = int(index.max()) + 1
            updated = np.zeros((local.shape[1], size))
            for outcome in range(OUTCOMES):
                # Within one outcome the shifted totals are distinct, so plain
                # fancy-index addition does not drop repeated indices.
                updated[:, index[outcome]] += moved[:, outcome, :]
            if i < len(weights):
                updated *= weights[i][:, None]
            message = updated
        return message[0]


@dataclass
class Registers:
    """Registers for feasible scenarios (lower) and dominating bounds (upper)."""

    mode: str  # "exact" or "rounded"
    lower: object
    upper: object
    description: str
    tick_units: int | None = None
    rounding_bound_units: int = 0

    @property
    def exact(self) -> bool:
        return self.mode == "exact"

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "register": self.lower.kind,
            "description": self.description,
            "tick": None if self.tick_units is None else format_usd(self.tick_units),
            "rounding_bound": format_usd(self.rounding_bound_units),
            "payout_levels": int(self.upper.size),
        }


def _nice_ticks():
    cents = 1
    while True:
        for factor in (1, 2, 5):
            yield factor * cents * UNITS_PER_CENT
        cents *= 10


def choose_registers(pool: RegionalPool, rounding_tick_units: int | None = None) -> Registers:
    pay = pool.payout_table()
    total = int(pay.max(axis=1).sum())
    if rounding_tick_units is None:
        nonzero = [int(x) for x in np.unique(pay) if x > 0]
        step = reduce(math.gcd, nonzero, 0) or UNITS_PER_CENT
        if total // step + 1 <= LATTICE_LIMIT:
            register = LatticeRegister(pay // step, step)
            return Registers(
                "exact",
                register,
                register,
                f"Exact amounts on a {format_usd(step)} payout lattice "
                f"({register.size:,} payout levels).",
                tick_units=step,
            )
        distinct = math.prod(len(np.unique(row)) for row in pay)
        if distinct <= SPARSE_LIMIT:
            try:
                register = SparseRegister(pay)
            except OverflowError:
                register = None
            if register is not None:
                return Registers(
                    "exact",
                    register,
                    register,
                    f"Exact amounts for every distinct payout total ({register.size:,} totals).",
                )
        rounding_tick_units = next(
            t for t in _nice_ticks() if total // t + 1 <= ROUNDED_TARGET_BINS
        )
    tick = int(rounding_tick_units)
    if tick <= 0:
        raise ValueError("rounding tick must be positive")
    floor = pay // tick
    ceil = -((-pay) // tick)
    if np.array_equal(floor, ceil):
        register = LatticeRegister(floor, tick)
        return Registers(
            "exact",
            register,
            register,
            f"Exact amounts on a {format_usd(tick)} payout lattice "
            f"({register.size:,} payout levels).",
            tick_units=tick,
        )
    bound = int((ceil - floor).max(axis=1).sum()) * tick
    return Registers(
        "rounded",
        LatticeRegister(floor, tick),
        LatticeRegister(ceil, tick),
        f"Payouts rounded to a {format_usd(tick)} lattice: down for feasible scenarios (lower "
        f"bound) and up for dominating models (upper bound). The aggregate rounding gap is at "
        f"most {format_usd(bound)}.",
        tick_units=tick,
        rounding_bound_units=bound,
    )


def distribution(pool: RegionalPool, rates: Rates, register) -> Curve:
    tensors, weights = local_tensors(pool, rates)
    return Curve(register.support_units, register.contract(tensors, weights))
