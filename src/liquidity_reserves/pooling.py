"""Pooling benefit: each country alone against the pooled reserve.

Standalone reserves come from the same certified procedure applied to a pool
of one country (no shared sources, so the bracket closes at once). The pooled
reserve is the certified reserve across the shared-shock range. The saving is
the diversification a regional pool buys, the quantity regional pools such as
ARC and CCRIF report (Ghesquiere and Mahul 2010; CCRIF 2025 brief).

The pooled reserve is then split between members two ways:

- standalone share: in proportion to each country's standalone reserve;
- shortfall contribution (an Euler-style tail allocation): in proportion to
  each country's expected payout in the outcomes where pooled payouts exceed
  the largest amount that still breaches the limit, E[X_i | X > a]. These are
  exact contractions (insights.shortfall_drivers), under the scenario that
  sets the lower bound.
"""

from __future__ import annotations

import numpy as np

from .bounds import bracket_reserve
from .contraction import choose_registers
from .insights import shortfall_drivers
from .money import UNITS_PER_USD
from .portfolio import RegionalPool, SharedShockRange


def standalone_pool(pool: RegionalPool, index: int) -> RegionalPool:
    country = pool.countries[index]
    return RegionalPool(
        name=f"{country.code} alone",
        countries=(country,),
        term_years=pool.term_years,
        base_shared_earthquake=(),
        base_point=pool.base_point,
        synthetic=pool.synthetic,
    )


def pooling_benefit(
    pool: RegionalPool, shock_range: SharedShockRange, risk_limit: float, result=None
) -> dict:
    registers = choose_registers(pool)
    result = result or bracket_reserve(pool, shock_range, risk_limit, registers=registers)
    pooled = result.upper_units / UNITS_PER_USD
    standalone = []
    for i, country in enumerate(pool.countries):
        alone = standalone_pool(pool, i)
        single = bracket_reserve(alone, SharedShockRange((0.0, 0.0), (0.0, 0.0)), risk_limit)
        standalone.append(
            {
                "country": country.code,
                "name": country.name,
                "reserve_usd": single.upper_units / UNITS_PER_USD,
                "cap_usd": country.principal_units / UNITS_PER_USD,
            }
        )
    total = sum(row["reserve_usd"] for row in standalone)
    # Largest attainable amount that still breaches the limit somewhere in the range.
    witness = result.witness
    support = witness.curve.support_units
    j = int(np.searchsorted(support, result.lower_units, side="left")) - 1
    amount = int(support[max(j, 0)])
    drivers = shortfall_drivers(pool, pool.rates(witness.point), registers.lower, amount)
    tail_total = (
        sum(row["expected_payout_given_shortfall_usd"] for row in drivers["countries"]) or 1.0
    )
    for row, driver in zip(standalone, drivers["countries"], strict=True):
        row["standalone_share_usd"] = pooled * row["reserve_usd"] / total if total else 0.0
        row["shortfall_contribution_usd"] = (
            pooled * driver["expected_payout_given_shortfall_usd"] / tail_total
        )
        row["expected_payout_given_shortfall_usd"] = driver["expected_payout_given_shortfall_usd"]
    return {
        "pool": pool.name,
        "risk_limit": risk_limit,
        "pooled_reserve_usd": pooled,
        "pooled_status": result.status,
        "standalone_total_usd": total,
        "saving_usd": total - pooled,
        "saving_percent": 100 * (total - pooled) / total if total else 0.0,
        "members": standalone,
        "allocation_amount_usd": amount / UNITS_PER_USD,
        "allocation_scenario": (
            f"shared earthquake rate {witness.point[0]:g} x base, shared rain "
            f"{100 * witness.point[1]:g}% of the smaller neighbouring rain rate"
        ),
        "evaluations": result.total_evaluations + drivers.get("evaluations", 0),
    }
