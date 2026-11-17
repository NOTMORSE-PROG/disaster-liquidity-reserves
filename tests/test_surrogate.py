import numpy as np
import pytest

from liquidity_reserves.contraction import choose_registers
from liquidity_reserves.examples import DEFAULT_RANGE
from liquidity_reserves.insights import assumption_map
from liquidity_reserves.surrogate import GaussianProcess, surrogate_map


def test_gp_interpolates_and_reports_uncertainty_away_from_data():
    rng = np.random.default_rng(1)
    x = rng.uniform(size=(12, 2))
    y = np.sin(3 * x[:, 0]) + x[:, 1] ** 2
    gp = GaussianProcess().fit(x, y)
    mean, sd = gp.predict(x)
    assert np.max(np.abs(mean - y)) < 1e-3
    _, far = gp.predict(np.array([[3.0, 3.0]]))
    assert far[0] > 10 * sd.max()


def test_shared_kernel_predictions_match_single_output(two_bond):
    rng = np.random.default_rng(2)
    x = rng.uniform(size=(10, 2))
    ys = np.vstack([x[:, 0] + x[:, 1], 2 * x[:, 0] - x[:, 1]])
    gp = GaussianProcess().fit(x, ys[0])
    means, _ = gp.predict_many(ys, x)
    assert np.allclose(means, ys, atol=1e-3)


@pytest.mark.parametrize("limit", [0.015, 0.005])
def test_surrogate_reproduces_the_exact_map_with_far_fewer_contractions(two_bond, limit):
    registers = choose_registers(two_bond)
    from liquidity_reserves.bounds import bracket_reserve

    result = bracket_reserve(two_bond, DEFAULT_RANGE, limit, registers=registers)
    exact = assumption_map(two_bond, DEFAULT_RANGE, limit, registers.lower, result.upper_units, 21)
    fast = surrogate_map(two_bond, DEFAULT_RANGE, limit, registers.lower, result.upper_units, 21)
    assert fast["evaluations"] <= 30 < exact["evaluations"]
    assert np.array_equal(np.array(fast["reserve_usd"]), np.array(exact["reserve_usd"]))
