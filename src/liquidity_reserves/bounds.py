"""Bracket the smallest reserve that meets a risk limit across a range of shared shocks.

Lower bound: a feasible scenario (corner, cell centre or base point) whose
exceedance at an amount is above the limit proves that every smaller reserve
is insufficient somewhere in the range.

Upper bound: each cell of the range gets a dominating model (shared rates at
the cell's high corner, private rates at its low corner). Its exceedance
bounds every feasible scenario in the cell, so a reserve that meets the limit
for every cell's dominating model is sufficient across the range.

Adaptive refinement splits the cell with the highest upper bound until the
bounds meet, the gap tolerance is reached or the evaluation budget runs out.

Counting rule: one "distribution evaluation" is one aggregate payout
distribution (one tensor contraction). The base point, each distinct corner,
each cell centre and each dominating model count once.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field

import numpy as np

from .contraction import Registers, choose_registers, distribution
from .curve import Curve
from .money import format_usd
from .portfolio import InputError, RegionalPool, SharedShockRange

# Floating-point allowance on probabilities. Summation error in these
# contractions is many orders smaller. A scenario whose exceedance is within
# this distance of the limit is left unresolved instead of being decided by
# rounding noise. It is a numerical allowance, not interval arithmetic.
GUARD = 1e-12
DEFAULT_MAX_EVALUATIONS = 2051  # matches a 1,023-cell refinement budget
MAX_EVALUATIONS_LIMIT = 20_000


@dataclass
class Scenario:
    point: tuple
    role: str  # base, corner or centre
    curve: Curve
    lower_units: int  # smallest amount this scenario does not prove insufficient


@dataclass
class Cell:
    low: np.ndarray
    high: np.ndarray
    curve: Curve  # dominating model
    upper_units: int


@dataclass
class Tracker:
    """Pointwise worst case over every feasible scenario evaluated."""

    support_units: np.ndarray
    worst_tail: np.ndarray = None
    worst_excess: np.ndarray = None
    points: list = field(default_factory=list)
    worst_index: np.ndarray = None

    def add(self, point, curve: Curve):
        index = len(self.points)
        self.points.append(tuple(float(x) for x in point))
        if self.worst_tail is None:
            self.worst_tail = curve.tail.copy()
            self.worst_excess = curve.excess.copy()
            self.worst_index = np.zeros(len(curve.tail), dtype=np.int64)
            return
        self.worst_index[curve.tail > self.worst_tail] = index
        np.maximum(self.worst_tail, curve.tail, out=self.worst_tail)
        np.maximum(self.worst_excess, curve.excess, out=self.worst_excess)

    def _position(self, amount_units: int) -> int:
        return int(np.searchsorted(self.support_units, amount_units, side="right")) - 1

    def exceedance_at(self, amount_units: int):
        """Highest observed P(L > amount) and the scenario that produced it."""
        j = self._position(amount_units)
        return float(self.worst_tail[j]), self.points[int(self.worst_index[j])]

    def excess_at(self, amount_units: int) -> float:
        """Highest observed E[max(L - amount, 0)] in USD."""
        j = self._position(amount_units)
        gap = (self.support_units[j] - amount_units) / 400
        return float(max(self.worst_excess[j] + gap * self.worst_tail[j], 0.0))


@dataclass
class BracketResult:
    method: str
    pool: RegionalPool
    shock_range: SharedShockRange
    risk_limit: float
    registers: Registers
    base: Scenario
    base_upper_units: int  # differs from base.lower_units only with rounding
    base_upper_curve: Curve
    lower_units: int
    upper_units: int
    witness: Scenario
    leaves: list
    tracker: Tracker
    evaluations: dict
    status: str  # resolved, within_gap, budget, resolution
    gap_units: int
    max_evaluations: int

    @property
    def resolved(self) -> bool:
        return self.lower_units == self.upper_units

    @property
    def total_evaluations(self) -> int:
        return int(sum(self.evaluations.values()))

    def binding_cell(self) -> Cell:
        return max(self.leaves, key=lambda c: c.curve.exceedance(self.upper_units))

    def upper_envelope(self) -> np.ndarray:
        return np.max([cell.curve.tail for cell in self.leaves], axis=0)

    def bound_at(self, amount_units: int) -> float:
        """Certified upper bound on P(L > amount) over the whole range."""
        return max(cell.curve.exceedance(amount_units) for cell in self.leaves)

    def shortfall_evidence(self):
        """Next attainable amount below the lower bound and its exceedance at the witness."""
        curve = self.witness.curve
        j = int(np.searchsorted(curve.support_units, self.lower_units, side="left"))
        if j == 0:
            return None
        return int(curve.support_units[j - 1]), float(curve.tail[j - 1])


class ReserveEngine:
    """Counts distribution evaluations and routes them to the right register."""

    def __init__(self, pool: RegionalPool, registers: Registers | None = None, method="tensor"):
        self.pool = pool
        self.registers = registers or choose_registers(pool)
        self.method = method
        self.evaluations = {"base": 0, "corner": 0, "centre": 0, "dominating": 0}
        if method == "ctmc":
            from .exact import ctmc_curve

            if not self.registers.exact:
                raise ValueError("the independent state-space check needs exact payouts")
            self._ctmc = ctmc_curve
        elif method != "tensor":
            raise ValueError("method must be 'tensor' or 'ctmc'")

    def _curve(self, rates, register) -> Curve:
        if self.method == "ctmc":
            return self._ctmc(self.pool, rates, register.support_units)
        return distribution(self.pool, rates, register)

    def feasible(self, point, role: str) -> Curve:
        self.evaluations[role] += 1
        return self._curve(self.pool.rates(point), self.registers.lower)

    def base_upper(self, point) -> Curve:
        self.evaluations["base"] += 1
        return self._curve(self.pool.rates(point), self.registers.upper)

    def dominating(self, low, high) -> Curve:
        self.evaluations["dominating"] += 1
        return self._curve(self.pool.dominating_rates(low, high), self.registers.upper)


def validate_request(pool, shock_range, risk_limit, gap_usd, max_evaluations):
    problems = []
    if isinstance(risk_limit, bool) or not isinstance(risk_limit, (int, float)):
        problems.append("risk limit must be a number")
    elif not 0 < risk_limit < 1:
        problems.append("risk limit must be strictly between 0% and 100%")
    if not isinstance(max_evaluations, int) or not 7 <= max_evaluations <= MAX_EVALUATIONS_LIMIT:
        problems.append(
            f"evaluation budget must be a whole number from 7 to {MAX_EVALUATIONS_LIMIT:,}"
        )
    if gap_usd is not None and (not np.isfinite(gap_usd) or gap_usd < 0):
        problems.append("gap tolerance must be zero or a positive amount")
    problems += [f"Range: {p}" for p in pool.infeasibilities(shock_range.high)]
    problems += [f"Base assumptions: {p}" for p in pool.infeasibilities(pool.base_point)]
    if problems:
        raise InputError(problems)


def _scenario(engine, point, role, risk_limit, tracker=None) -> Scenario:
    curve = engine.feasible(point, role)
    if tracker is not None:
        tracker.add(point, curve)
    lower = curve.reserve_units(risk_limit, shift=-GUARD)
    return Scenario(tuple(float(x) for x in point), role, curve, lower)


def _cell(engine, low, high, risk_limit) -> Cell:
    curve = engine.dominating(low, high)
    return Cell(low, high, curve, curve.reserve_units(risk_limit, shift=GUARD))


def _start(pool, shock_range, risk_limit, engine):
    tracker = Tracker(engine.registers.lower.support_units)
    point = tuple(float(x) for x in pool.base_point)
    curve = engine.feasible(point, "base")
    base = Scenario(point, "base", curve, curve.reserve_units(risk_limit))
    if engine.registers.exact:
        base_upper_curve, base_upper_units = curve, base.lower_units
    else:
        base_upper_curve = engine.base_upper(point)
        base_upper_units = base_upper_curve.reserve_units(risk_limit)
    witnesses = []
    # The base point is a feasible witness only when it lies inside the range.
    if shock_range.contains(point):
        tracker.add(point, curve)
        witnesses.append(Scenario(point, "base", curve, curve.reserve_units(risk_limit, -GUARD)))
    for corner in shock_range.corners():
        witnesses.append(_scenario(engine, corner, "corner", risk_limit, tracker))
    return tracker, base, base_upper_units, base_upper_curve, witnesses


def _evidence(scenario: Scenario) -> float:
    """Exceedance at the next attainable amount below the scenario's lower bound."""
    curve = scenario.curve
    j = int(np.searchsorted(curve.support_units, scenario.lower_units, side="left"))
    return float(curve.tail[j - 1]) if j > 0 else 0.0


def _best(witnesses):
    """Highest lower bound; ties go to the scenario with the clearest shortfall."""
    return max(witnesses, key=lambda s: (s.lower_units, _evidence(s)))


def bracket_reserve(
    pool: RegionalPool,
    shock_range: SharedShockRange,
    risk_limit: float,
    *,
    gap_usd: float = 0.0,
    max_evaluations: int = DEFAULT_MAX_EVALUATIONS,
    registers: Registers | None = None,
    method: str = "tensor",
) -> BracketResult:
    """Adaptive refinement of the reserve bracket."""
    validate_request(pool, shock_range, risk_limit, gap_usd, max_evaluations)
    engine = ReserveEngine(pool, registers, method)
    gap_units = int(round(gap_usd * 400))
    tracker, base, base_upper_units, base_upper_curve, witnesses = _start(
        pool, shock_range, risk_limit, engine
    )
    importance = pool.rate_axes()
    root_low, root_high = shock_range.low, shock_range.high
    span = float(np.max((root_high - root_low) * importance))
    heap, sequence = [], 0

    def add(low, high):
        nonlocal sequence
        witnesses.append(_scenario(engine, (low + high) / 2, "centre", risk_limit, tracker))
        cell = _cell(engine, low, high, risk_limit)
        sequence += 1
        heapq.heappush(heap, (-cell.upper_units, sequence, cell))

    add(root_low, root_high)
    status = "budget"
    while True:
        best = _best(witnesses)
        top = heap[0][2]
        if top.upper_units <= best.lower_units:
            status = "resolved"
            break
        if top.upper_units - best.lower_units <= gap_units:
            status = "within_gap"
            break
        if sum(engine.evaluations.values()) + 4 > max_evaluations:
            status = "budget"
            break
        widths = (top.high - top.low) * importance
        if span == 0 or widths.max() <= 1e-12 * span:
            status = "resolution"
            break
        heapq.heappop(heap)
        axis = int(np.argmax(widths))
        split = top.low[axis] + (top.high[axis] - top.low[axis]) / 2
        left_high, right_low = top.high.copy(), top.low.copy()
        left_high[axis] = right_low[axis] = split
        add(top.low, left_high)
        add(right_low, top.high)
    best = _best(witnesses)
    leaves = [entry[2] for entry in heap]
    upper = max(cell.upper_units for cell in leaves)
    # A witness can exceed the bound only through the guard band; keep the
    # bracket ordered and report it as unresolved in that case.
    lower = min(best.lower_units, upper)
    if lower == upper and status != "resolved":
        status = "resolved"
    return BracketResult(
        method="adaptive refinement" + (" (state-space check)" if method == "ctmc" else ""),
        pool=pool,
        shock_range=shock_range,
        risk_limit=risk_limit,
        registers=engine.registers,
        base=base,
        base_upper_units=base_upper_units,
        base_upper_curve=base_upper_curve,
        lower_units=lower,
        upper_units=upper,
        witness=best,
        leaves=leaves,
        tracker=tracker,
        evaluations=dict(engine.evaluations),
        status=status,
        gap_units=gap_units,
        max_evaluations=max_evaluations,
    )


def grid_reserve(
    pool: RegionalPool,
    shock_range: SharedShockRange,
    risk_limit: float,
    *,
    subdivisions: int = 16,
    registers: Registers | None = None,
) -> BracketResult:
    """Uniform k-by-k grid: every cell gets a centre scenario and a dominating bound."""
    if not isinstance(subdivisions, int) or not 1 <= subdivisions <= 64:
        raise InputError("grid subdivisions must be a whole number from 1 to 64")
    validate_request(pool, shock_range, risk_limit, 0.0, MAX_EVALUATIONS_LIMIT)
    engine = ReserveEngine(pool, registers)
    tracker, base, base_upper_units, base_upper_curve, witnesses = _start(
        pool, shock_range, risk_limit, engine
    )
    low, high = shock_range.low, shock_range.high
    leaves = []
    for i in range(subdivisions):
        for j in range(subdivisions):
            cell_low = low + (high - low) * np.array([i, j]) / subdivisions
            cell_high = low + (high - low) * np.array([i + 1, j + 1]) / subdivisions
            witnesses.append(
                _scenario(engine, (cell_low + cell_high) / 2, "centre", risk_limit, tracker)
            )
            leaves.append(_cell(engine, cell_low, cell_high, risk_limit))
    best = _best(witnesses)
    upper = max(cell.upper_units for cell in leaves)
    lower = min(best.lower_units, upper)
    return BracketResult(
        method=f"uniform {subdivisions}x{subdivisions} grid",
        pool=pool,
        shock_range=shock_range,
        risk_limit=risk_limit,
        registers=engine.registers,
        base=base,
        base_upper_units=base_upper_units,
        base_upper_curve=base_upper_curve,
        lower_units=lower,
        upper_units=upper,
        witness=best,
        leaves=leaves,
        tracker=tracker,
        evaluations=dict(engine.evaluations),
        status="resolved" if lower == upper else "grid",
        gap_units=0,
        max_evaluations=MAX_EVALUATIONS_LIMIT,
    )


def summarize(result: BracketResult) -> str:
    if result.resolved:
        return (
            f"Minimum reserve across the range: {format_usd(result.upper_units)} "
            f"({result.total_evaluations} evaluations)"
        )
    return (
        f"Minimum reserve between {format_usd(result.lower_units)} and "
        f"{format_usd(result.upper_units)} "
        f"({result.total_evaluations} evaluations, {result.status})"
    )
