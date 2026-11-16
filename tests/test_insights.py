import numpy as np
import pytest

from liquidity_reserves.bounds import bracket_reserve
from liquidity_reserves.contraction import choose_registers, distribution
from liquidity_reserves.examples import DEFAULT_RANGE
from liquidity_reserves.insights import (
    assumption_map,
    chain_graph,
    limit_sweep,
    shortfall_drivers,
)
from liquidity_reserves.money import usd_to_units


def test_assumption_map_stays_inside_the_certified_bracket(two_bond):
    result = bracket_reserve(two_bond, DEFAULT_RANGE, 0.015)
    registers = choose_registers(two_bond)
    grid = assumption_map(two_bond, DEFAULT_RANGE, 0.015, registers.lower, result.upper_units, 9)
    reserves = np.array(grid["reserve_usd"])
    assert reserves.shape == (9, 9) and grid["evaluations"] == 81
    # Every map point is a feasible scenario, so none can need more than the upper bound.
    assert reserves.max() * 400 <= result.upper_units
    assert reserves.max() * 400 >= result.lower_units  # corners are on the grid
    exceed = np.array(grid["exceedance"])
    assert exceed.max() <= result.bound_at(result.upper_units) + 1e-15


def test_shortfall_drivers_add_up_to_the_conditional_mean(chain5):
    registers = choose_registers(chain5)
    rates = chain5.rates((1.5, 0.2))
    amount = usd_to_units(120_000_000)
    drivers = shortfall_drivers(chain5, rates, registers.lower, amount)
    curve = distribution(chain5, rates, registers.lower)
    above = curve.support_units > amount
    probability = curve.probabilities[above].sum()
    conditional_mean = (curve.probabilities[above] * curve.support_units[above] / 400).sum()
    conditional_mean /= probability
    total = sum(r["expected_payout_given_shortfall_usd"] for r in drivers["countries"])
    assert drivers["probability_exceeded"] == pytest.approx(probability, rel=1e-12)
    assert total == pytest.approx(conditional_mean, rel=1e-10)
    assert sum(r["share_of_shortfall_payouts"] for r in drivers["countries"]) == pytest.approx(1)
    for row in drivers["countries"]:
        assert 0 <= row["probability_pays_given_shortfall"] <= 1 + 1e-12
        assert row["expected_payout_given_shortfall_usd"] <= row["cap_usd"] + 1e-6


def test_limit_sweep_is_monotone(two_bond):
    registers = choose_registers(two_bond)
    rows = limit_sweep(two_bond, DEFAULT_RANGE, [0.005, 0.01, 0.015, 0.02], registers)
    uppers = [r["upper_usd"] for r in rows]
    bases = [r["base_usd"] for r in rows]
    assert uppers == sorted(uppers, reverse=True)
    assert bases == sorted(bases, reverse=True)
    assert rows[0]["upper_usd"] == 105_000_000 and rows[2]["upper_usd"] == 87_500_000
    assert all(r["base_usd"] <= r["upper_usd"] for r in rows)


def test_chain_graph_has_one_link_per_neighbouring_pair(chain5):
    graph = chain_graph(chain5, DEFAULT_RANGE, (1.0, 0.25))
    assert len(graph["nodes"]) == 5 and len(graph["links"]) == 4
    link = graph["links"][0]
    assert link["earthquake_range"][0] == 0 and link["earthquake_range"][1] > 0
    assert link["earthquake_range"][0] <= link["earthquake_at_point"] <= link["earthquake_range"][1]
