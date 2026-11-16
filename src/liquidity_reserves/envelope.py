"""Two-country grid envelope with a Poisson coupling bound (independent check).

For two countries the earthquake law depends only on the earthquake
multiplier and the precipitation law only on the rain share, so a fine grid
over both axes is cheap. Joint earthquake laws come from the Markov generator
(matrix exponential), not from the tensor contraction.

Between grid nodes, moving the shared rate by d changes three Poisson sources
per peril by d each (the shared source and both private sources). Coupling
the matched parts, two models differ only if one of those extra sources
fires, so any event's probability changes by at most
1 - exp(-3 T (d_quake + d_rain)). With d at most half a grid step, the grid
maximum plus that amount bounds the supremum over the whole rectangle.
"""

from __future__ import annotations

import numpy as np
from scipy.linalg import expm

from .bounds import GUARD
from .portfolio import RegionalPool, SharedShockRange


def _quake_generator(pool: RegionalPool, shared: float) -> np.ndarray:
    severity = np.array([c.severity for c in pool.countries])
    totals = pool.earthquake_marginal
    jumps = np.zeros((5, 5))
    jumps[1:, 0] = (totals[0] - shared) * severity[0]
    jumps[0, 1:] = (totals[1] - shared) * severity[1]
    jumps[1:, 1:] = shared * np.outer(severity[0], severity[1])
    states = np.array(list(np.ndindex(5, 5)))
    rows = np.arange(25)
    matrix = np.zeros((25, 25))
    for a, b in np.argwhere(jumps > 0):
        target = np.minimum(states + [a, b], 4)
        matrix[rows, target[:, 0] * 5 + target[:, 1]] += jumps[a, b]
        matrix[rows, rows] -= jumps[a, b]
    return matrix


def two_country_envelope(
    pool: RegionalPool, shock_range: SharedShockRange, risk_limits, cells: int = 200
) -> dict:
    if pool.n != 2:
        raise ValueError("the grid envelope applies to two-country pools")
    years = float(pool.term_years)
    multipliers = np.linspace(*shock_range.earthquake_multiplier, cells + 1)
    shares = np.linspace(*shock_range.rain_share, cells + 1)
    base_shared = float(pool.base_shared_earthquake[0])
    generators = np.array([_quake_generator(pool, base_shared * m) for m in multipliers])
    quake = expm(generators * years)[:, 0, :]  # rows: multiplier; columns: (k1, k2)
    rho = pool.rain_marginal
    common = shares * min(rho)
    neither = np.exp(-(rho.sum() - common) * years)
    first_only = np.exp(-rho[1] * years) - neither
    second_only = np.exp(-rho[0] * years) - neither
    both = 1 - neither - first_only - second_only
    rain = np.column_stack((neither, second_only, first_only, both))
    pay = pool.payout_table()  # outcome index 2k + r
    loss = (pay[0][:, None] + pay[1][None, :]).reshape(5, 2, 5, 2).transpose(0, 2, 1, 3)
    loss = loss.reshape(25, 4)  # rows (k1, k2); columns (r1, r2)
    knots = np.unique(loss)
    worst = np.zeros(len(knots))
    worst_excess = np.zeros(len(knots))
    witness = []
    for j, knot in enumerate(knots):
        exceed = quake @ (loss > knot) @ rain.T
        excess = quake @ (np.maximum(loss - knot, 0) / 400) @ rain.T
        worst[j] = exceed.max()
        worst_excess[j] = excess.max()
        a, b = np.unravel_index(exceed.argmax(), exceed.shape)
        witness.append((float(multipliers[a]), float(shares[b])))
    step_quake = base_shared * (multipliers[-1] - multipliers[0]) / cells
    step_rain = min(rho) * (shares[-1] - shares[0]) / cells
    coupling = float(-np.expm1(-3 * years * (step_quake + step_rain) / 2))
    upper = np.minimum(1.0, worst + coupling)
    upper[-1] = 0.0  # nothing exceeds the largest attainable payout
    reserves = []
    for limit in risk_limits:
        reserves.append(
            {
                "risk_limit": limit,
                "lower_units": int(knots[np.flatnonzero(worst - GUARD <= limit)[0]]),
                "upper_units": int(knots[np.flatnonzero(upper + GUARD <= limit)[0]]),
            }
        )
    return {
        "cells_per_axis": cells,
        "grid_points": (cells + 1) ** 2,
        "coupling_bound": coupling,
        "knots_units": knots,
        "worst_exceedance": worst,
        "bounded_exceedance": upper,
        "worst_excess_usd": worst_excess,
        "witness": witness,
        "reserves": reserves,
    }
