import math

import numpy as np
import pytest

from liquidity_reserves.bounds import bracket_reserve
from liquidity_reserves.examples import DEFAULT_RANGE
from liquidity_reserves.quantum import (
    grover_probabilities,
    loaded_state,
    mlae,
    queries_for_precision,
    query_study,
)


@pytest.fixture(scope="module")
def base(two_bond):
    result = bracket_reserve(two_bond, DEFAULT_RANGE, 0.015)
    return result.base.curve, result.base.lower_units


def test_loaded_state_encodes_the_exceedance_probability(base):
    curve, amount = base
    amplitudes, mask, width = loaded_state(curve, amount)
    assert len(amplitudes) == 1 << width
    assert np.sum(amplitudes**2) == pytest.approx(1, abs=1e-14)
    assert np.sum(amplitudes[mask] ** 2) == pytest.approx(curve.exceedance(amount), abs=1e-15)


def test_grover_powers_follow_the_amplitude_rotation(base):
    curve, amount = base
    amplitudes, mask, _ = loaded_state(curve, amount)
    powers = [0, 1, 2, 5, 13]
    theta = math.asin(math.sqrt(curve.exceedance(amount)))
    expected = np.sin((2 * np.array(powers) + 1) * theta) ** 2
    assert np.max(np.abs(grover_probabilities(amplitudes, mask, powers) - expected)) < 1e-13


def test_mlae_recovers_the_amplitude_from_exact_counts():
    powers = [0, 1, 2, 4, 8]
    truth = 0.0123
    theta = math.asin(math.sqrt(truth))
    shots = np.full(len(powers), 10**6)
    hits = np.rint(shots * np.sin((2 * np.array(powers) + 1) * theta) ** 2)
    assert mlae(hits, shots, powers) == pytest.approx(truth, rel=1e-3)
    assert mlae([0, 0], [10, 10], [0, 1]) == 0.0


def test_query_study_beats_monte_carlo_at_depth(base):
    curve, amount = base
    study = query_study(curve, amount, schedules=6, repeats=40)
    assert study["statevector_vs_analytic_max_error"] < 1e-12
    deepest = study["rows"][-1]
    assert deepest["qae_rmse"] < deepest["monte_carlo_rmse"]
    queries = [r["queries"] for r in study["rows"]]
    assert queries == sorted(queries)


def test_precision_requirements():
    row = queries_for_precision(0.012, 1e-3)
    assert row["monte_carlo_queries_95"] == math.ceil(1.959963984540054**2 * 0.012 * 0.988 / 1e-6)
    assert row["qae_grover_depth"] & (row["qae_grover_depth"] - 1) == 0  # power of two
    assert row["qae_queries_95"] < row["monte_carlo_queries_95"]


def test_compiled_resources_when_qiskit_is_installed(base):
    pytest.importorskip("qiskit")
    from liquidity_reserves.quantum import compiled_resources

    curve, amount = base
    resources = compiled_resources(curve, amount)
    q = resources["circuits"]["grover_iterate_Q"]
    assert resources["qubits"] == 6
    assert q["cx"] > resources["circuits"]["loading_A"]["cx"] > 0
