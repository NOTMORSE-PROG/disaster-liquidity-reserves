"""Independent exact calculation on the full capped country-state space.

This is a separate method from the tensor contraction: it builds the
continuous-time Markov generator on every joint capped state (earthquake
quarter steps 0..4 and precipitation trigger 0/1 per country) and applies the
matrix exponential over the term. It shares no code with the contraction
beyond the input rates, so agreement between the two is a real cross-check.
It is limited to five countries (5^5 earthquake states).
"""

from __future__ import annotations

from itertools import product

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.linalg import expm_multiply

from .curve import Curve
from .portfolio import Rates, RegionalPool

MAX_COUNTRIES = 5


def _terminal_law(shape, sources: dict, years: float):
    states = np.array(list(np.ndindex(*shape)), dtype=np.int64)
    index = np.arange(len(states))
    caps = np.asarray(shape) - 1
    rows, cols, values = [index], [index], [np.full(len(states), -sum(sources.values()))]
    for jump, rate in sources.items():
        if rate <= 0:
            continue
        target = np.minimum(states + np.asarray(jump), caps)
        rows.append(np.ravel_multi_index(target.T, shape))
        cols.append(index)
        values.append(np.full(len(states), rate))
    generator = coo_matrix(
        (np.concatenate(values), (np.concatenate(rows), np.concatenate(cols))),
        shape=(len(states), len(states)),
    ).tocsr()
    start = np.zeros(len(states))
    start[0] = 1.0
    law = expm_multiply(generator * years, start)
    return states, np.maximum(law, 0.0)


def joint_laws(pool: RegionalPool, rates: Rates):
    """Terminal laws of the earthquake and precipitation country-state vectors."""
    n = pool.n
    if n > MAX_COUNTRIES:
        raise ValueError(f"the state-space check supports at most {MAX_COUNTRIES} countries")
    unit = np.eye(n, dtype=np.int64)
    severity = np.array([c.severity for c in pool.countries])
    quake, rain = {}, {}

    def add(table, jump, rate):
        key = tuple(int(x) for x in jump)
        table[key] = table.get(key, 0.0) + float(rate)

    for i in range(n):
        for step in range(1, 5):
            add(quake, step * unit[i], rates.earthquake_private[i] * severity[i, step - 1])
        add(rain, unit[i], rates.rain_private[i])
    for i in range(n - 1):
        for a, b in product(range(1, 5), repeat=2):
            add(
                quake,
                a * unit[i] + b * unit[i + 1],
                rates.earthquake_shared[i] * severity[i, a - 1] * severity[i + 1, b - 1],
            )
        add(rain, unit[i] + unit[i + 1], rates.rain_shared[i])
    years = float(pool.term_years)
    return _terminal_law((5,) * n, quake, years), _terminal_law((2,) * n, rain, years)


def ctmc_distribution(pool: RegionalPool, rates: Rates):
    """Exact (amounts in quarter-cents, probabilities) of the aggregate payout."""
    (quake_states, quake_law), (rain_states, rain_law) = joint_laws(pool, rates)
    quarter = np.array([c.earthquake_cap_units // 4 for c in pool.countries], dtype=np.int64)
    rain_cap = np.array([c.rain_cap_units for c in pool.countries], dtype=np.int64)
    total = (quake_states @ quarter)[:, None] + (rain_states @ rain_cap)[None, :]
    amounts, inverse = np.unique(total.ravel(), return_inverse=True)
    return amounts, np.bincount(inverse, weights=np.outer(quake_law, rain_law).ravel())


def ctmc_curve(pool: RegionalPool, rates: Rates, support_units: np.ndarray) -> Curve:
    """State-space distribution placed on a given exact support."""
    amounts, probabilities = ctmc_distribution(pool, rates)
    positions = np.searchsorted(support_units, amounts)
    if np.any(positions >= len(support_units)) or np.any(support_units[positions] != amounts):
        raise ValueError("state-space amounts are not on the supplied exact support")
    aligned = np.zeros(len(support_units))
    np.add.at(aligned, positions, probabilities)
    return Curve(support_units, aligned)
