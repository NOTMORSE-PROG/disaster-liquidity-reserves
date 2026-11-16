"""Decision insights built on the same contraction as the reserve bounds.

- assumption_map: the point reserve at every assumption on a grid over the
  shared-shock range, showing where dependence moves the decision.
- shortfall_drivers: which countries' payouts make up a shortfall, from
  payout-weighted contractions (exact, no sampling).
- limit_sweep: how the reserve bracket moves with the risk limit.

Map and driver evaluations are for explanation only. They are counted
separately and never enter the certified bounds.
"""

from __future__ import annotations

import numpy as np

from .bounds import bracket_reserve
from .contraction import local_tensors
from .curve import Curve
from .money import UNITS_PER_USD
from .portfolio import Rates, RegionalPool, SharedShockRange


def map_resolution(countries: int) -> int:
    """Grid points per axis, coarser for long chains to keep the map interactive."""
    if countries <= 5:
        return 21
    if countries <= 10:
        return 15
    return 11


def assumption_map(
    pool: RegionalPool,
    shock_range: SharedShockRange,
    risk_limit: float,
    register,
    amount_units: int,
    points: int | None = None,
) -> dict:
    """Point reserve and P(L > amount) at each assumption on a points x points grid."""
    points = points or map_resolution(pool.n)
    multipliers = np.linspace(*shock_range.earthquake_multiplier, points)
    shares = np.linspace(*shock_range.rain_share, points)
    reserve = np.zeros((points, points), dtype=np.int64)
    exceedance = np.zeros((points, points))
    for i, m in enumerate(multipliers):
        for j, s in enumerate(shares):
            tensors, weights = local_tensors(pool, pool.rates((m, s)))
            curve = Curve(register.support_units, register.contract(tensors, weights))
            reserve[i, j] = curve.reserve_units(risk_limit)
            exceedance[i, j] = curve.exceedance(amount_units)
    return {
        "earthquake_multiplier": multipliers.tolist(),
        "rain_share": shares.tolist(),
        "reserve_usd": (reserve / UNITS_PER_USD).tolist(),
        "exceedance": exceedance.tolist(),
        "amount_usd": amount_units / UNITS_PER_USD,
        "evaluations": points * points,
    }


def shortfall_drivers(pool: RegionalPool, rates: Rates, register, amount_units: int) -> dict:
    """Each country's share of payouts in the scenarios where L exceeds the amount.

    For country i, scaling its local tensor by its payout on each outcome turns
    the contraction into E[payout_i; L = x]. Summing above the amount gives
    E[payout_i 1{L > amount}]; masking to positive payouts gives
    P(country i pays, L > amount). Both are exact.
    """
    tensors, weights = local_tensors(pool, rates)
    support = register.support_units
    above = support > amount_units
    probability = float(register.contract(tensors, weights)[above].sum())
    pay = pool.payout_table()
    rows = []
    for i, country in enumerate(pool.countries):
        weighted = list(tensors)
        dollars = pay[i] / UNITS_PER_USD
        weighted[i] = tensors[i] * dollars[None, None, :]
        excess_payout = float(register.contract(weighted, weights)[above].sum())
        attached = list(tensors)
        attached[i] = tensors[i] * (pay[i] > 0)[None, None, :]
        pays = float(register.contract(attached, weights)[above].sum())
        rows.append(
            {
                "country": country.code,
                "name": country.name,
                "expected_payout_given_shortfall_usd": excess_payout / probability
                if probability > 0
                else 0.0,
                "probability_pays_given_shortfall": pays / probability if probability > 0 else 0.0,
                "cap_usd": country.principal_units / UNITS_PER_USD,
            }
        )
    total = sum(r["expected_payout_given_shortfall_usd"] for r in rows)
    for row in rows:
        row["share_of_shortfall_payouts"] = (
            row["expected_payout_given_shortfall_usd"] / total if total > 0 else 0.0
        )
    return {
        "amount_usd": amount_units / UNITS_PER_USD,
        "probability_exceeded": probability,
        "countries": rows,
        "evaluations": 1 + 2 * pool.n,
    }


def limit_sweep(
    pool: RegionalPool,
    shock_range: SharedShockRange,
    limits,
    registers,
    *,
    gap_usd: float = 0.0,
    max_evaluations: int = 2051,
) -> list:
    """Base reserve and range bracket for each risk limit."""
    rows = []
    for limit in limits:
        result = bracket_reserve(
            pool,
            shock_range,
            float(limit),
            gap_usd=gap_usd,
            max_evaluations=max_evaluations,
            registers=registers,
        )
        rows.append(
            {
                "risk_limit": float(limit),
                "base_usd": result.base.lower_units / UNITS_PER_USD,
                "lower_usd": result.lower_units / UNITS_PER_USD,
                "upper_usd": result.upper_units / UNITS_PER_USD,
                "resolved": result.resolved,
                "status": result.status,
                "evaluations": result.total_evaluations,
            }
        )
    return rows


def chain_graph(pool: RegionalPool, shock_range: SharedShockRange, point) -> dict:
    """Nodes and links for the regional map: marginal rates, caps and shared rates."""
    quake_low, rain_low = pool.shared_rates(shock_range.low)
    quake_high, rain_high = pool.shared_rates(shock_range.high)
    quake_at, rain_at = pool.shared_rates(point)
    return {
        "nodes": [
            {
                "code": c.code,
                "name": c.name,
                "earthquake_rate": c.earthquake_rate,
                "rain_rate": c.rain_rate,
                "cap_usd": c.principal_units / UNITS_PER_USD,
                "earthquake_cap_usd": c.earthquake_cap_units / UNITS_PER_USD,
                "rain_payment_usd": c.rain_cap_units / UNITS_PER_USD,
            }
            for c in pool.countries
        ],
        "links": [
            {
                "pair": label,
                "earthquake_range": [float(quake_low[k]), float(quake_high[k])],
                "rain_range": [float(rain_low[k]), float(rain_high[k])],
                "earthquake_at_point": float(quake_at[k]),
                "rain_at_point": float(rain_at[k]),
            }
            for k, label in enumerate(pool.pair_labels)
        ],
    }
