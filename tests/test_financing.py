import itertools

import numpy as np
import pytest

from liquidity_reserves.bounds import bracket_reserve
from liquidity_reserves.examples import DEFAULT_RANGE
from liquidity_reserves.layering import (
    CostParameters,
    excess_curve,
    optimize_layers,
    plan_cost,
    risk_layering,
)
from liquidity_reserves.pooling import pooling_benefit
from liquidity_reserves.portfolio import InputError


@pytest.fixture(scope="module")
def result(two_bond):
    return bracket_reserve(two_bond, DEFAULT_RANGE, 0.015)


def test_excess_curve_matches_the_curve_expectation(result):
    curve = result.base.curve
    g = excess_curve(curve.support_units, curve.tail)
    for j in (0, 5, 17, len(g) - 1):
        assert g[j] == pytest.approx(
            curve.expected_excess(int(curve.support_units[j])), rel=1e-12, abs=1e-6
        )


def _inputs(result):
    curve = result.base_upper_curve
    g = excess_curve(curve.support_units, curve.tail)
    levels = curve.support_units / 400
    top = int(np.searchsorted(curve.support_units, result.upper_units))
    return g, levels, top


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"insurance_multiple": 1.5},
        {"reserve_cost_per_year": 0.0},
        {"credit_capacity_charge_per_year": 0.2},
        {"credit_limit_usd": 30e6},
    ],
)
def test_scan_matches_brute_force(result, params):
    g, levels, top = _inputs(result)
    cost = CostParameters(**params)
    i1, i2 = optimize_layers(g, levels, top, 3.0, cost)
    best = plan_cost(g, levels, i1, i2, top, 3.0, cost)["total_cost_usd"]
    feasible = []
    for a, b in itertools.combinations_with_replacement(range(top + 1), 2):
        if (
            cost.credit_limit_usd is not None
            and levels[b] - levels[a] > cost.credit_limit_usd + 1e-6
        ):
            continue
        feasible.append(plan_cost(g, levels, a, b, top, 3.0, cost)["total_cost_usd"])
    assert best == pytest.approx(min(feasible), rel=1e-12)


def test_public_plan_uses_credit_then_insurance(two_bond, result):
    plan = risk_layering(two_bond, DEFAULT_RANGE, 0.015, result=result)
    layers = plan["optimal"]["layers_usd"]
    assert plan["capacity_usd"] == 87_500_000
    assert layers["credit"] == [0.0, 70_000_000.0]
    assert layers["insurance"] == [70_000_000.0, 87_500_000.0]
    costs = [a["total_cost_usd"] for a in plan["alternatives"].values()]
    assert plan["optimal"]["total_cost_usd"] <= min(costs) + 1e-6
    assert plan["saving_vs_all_reserve_percent"] > 10


def test_layer_payouts_add_up_to_the_capped_expectation(result):
    g, levels, top = _inputs(result)
    plan = plan_cost(g, levels, 3, 20, top, 3.0, CostParameters())
    assert sum(plan["expected_payout_usd"].values()) == pytest.approx(g[0] - g[top])


def test_bound_basis_never_costs_less_than_base(two_bond, result):
    base = risk_layering(two_bond, DEFAULT_RANGE, 0.015, basis="base", result=result)
    bound = risk_layering(two_bond, DEFAULT_RANGE, 0.015, basis="bound", result=result)
    assert bound["optimal"]["total_cost_usd"] >= base["optimal"]["total_cost_usd"]


def test_cost_parameters_are_validated():
    with pytest.raises(InputError):
        CostParameters(insurance_multiple=-1).validate()
    with pytest.raises(InputError):
        CostParameters(credit_limit_usd=float("nan")).validate()


def test_pooling_saves_capital_and_allocations_add_up(two_bond, result):
    pooled = pooling_benefit(two_bond, DEFAULT_RANGE, 0.015, result=result)
    assert [m["reserve_usd"] for m in pooled["members"]] == [70_000_000, 70_000_000]
    assert pooled["pooled_reserve_usd"] == 87_500_000
    assert pooled["saving_percent"] == pytest.approx(37.5)
    for key in ("standalone_share_usd", "shortfall_contribution_usd"):
        assert sum(m[key] for m in pooled["members"]) == pytest.approx(87_500_000)
