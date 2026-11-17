import math

import numpy as np
import pytest

pytest.importorskip("qiskit")
pytest.importorskip("qiskit_aer")

from liquidity_reserves.bounds import bracket_reserve  # noqa: E402
from liquidity_reserves.examples import DEFAULT_RANGE  # noqa: E402
from liquidity_reserves.layering import _layer_costs  # noqa: E402
from liquidity_reserves.noise import (  # noqa: E402
    error_budget,
    flag_probabilities,
    noise_aware_mlae,
    qae_circuits,
)
from liquidity_reserves.qaoa import (  # noqa: E402
    build_qubo,
    candidate_levels,
    decode,
    energies,
    layering_qaoa,
    plan_inputs,
    qubo_to_ising,
)


@pytest.fixture(scope="module")
def inputs(two_bond):
    return plan_inputs(two_bond, DEFAULT_RANGE, 0.015)


def test_ising_operator_reproduces_qubo_energies(inputs):
    from qiskit.quantum_info import Statevector

    h1, h2 = _layer_costs(
        inputs["g"], inputs["levels"], inputs["top"], inputs["term"], inputs["cost"]
    )
    options = candidate_levels(inputs["top"], 4)
    q, offset, _ = build_qubo(h1, h2, inputs["levels"], options)
    hamiltonian, constant = qubo_to_ising(q)
    values = energies(q, offset)
    diagonal = np.real(hamiltonian.to_matrix(sparse=True).diagonal())
    assert np.allclose(diagonal + constant + offset, values, atol=1e-9)
    assert Statevector.from_label("0" * len(q)).num_qubits == len(q)


def test_qubo_minimum_is_the_best_ordered_pair(inputs):
    h1, h2 = _layer_costs(
        inputs["g"], inputs["levels"], inputs["top"], inputs["term"], inputs["cost"]
    )
    options = candidate_levels(inputs["top"], 5)
    q, offset, _ = build_qubo(h1, h2, inputs["levels"], options)
    best = int(np.argmin(energies(q, offset)))
    x, y = decode(best, len(options))
    assert sum(x) == 1 and sum(y) == 1
    i, j = x.index(1), y.index(1)
    pairs = [
        (a, b) for a in range(len(options)) for b in range(len(options)) if options[a] <= options[b]
    ]
    exact = min(pairs, key=lambda p: h1[options[p[0]]] + h2[options[p[1]]])
    assert (i, j) == exact


def test_xy_mixer_qaoa_finds_the_optimal_stack(inputs):
    run = layering_qaoa(inputs, k=6, reps=3, shots=2000)
    assert run["qaoa_finds_optimum"]
    assert run["probability_optimal_sampled"] > 20 * run["uniform_probability_optimal"]
    assert run["brute_force_optimum"]["credit"] == [0.0, 70_000_000.0]


def test_ideal_noise_model_reproduces_the_amplitude_rotation(two_bond):
    result = bracket_reserve(two_bond, DEFAULT_RANGE, 0.015)
    circuits, truth = qae_circuits(result.base.curve, result.base.lower_units, powers=(0, 1, 3))
    probs, _ = flag_probabilities(circuits, 0.0)
    theta = math.asin(math.sqrt(truth))
    expected = np.sin((2 * np.array([0, 1, 3]) + 1) * theta) ** 2
    assert np.max(np.abs(probs - expected)) < 4e-3  # 200,000 seeded shots


def test_noise_aware_fit_recovers_a_decayed_signal():
    truth, lam, floor = 0.012, 0.02, 0.3
    powers = np.array([0, 1, 2, 4, 8])
    m = 2 * powers + 1
    decay = np.exp(-lam * m)
    p = decay * np.sin(m * math.asin(math.sqrt(truth))) ** 2 + (1 - decay) * floor
    shots = np.full(len(powers), 10**7)
    estimate, fitted_lam = noise_aware_mlae(np.rint(p * shots), shots, powers)
    assert estimate == pytest.approx(truth, rel=0.05)
    assert fitted_lam == pytest.approx(lam, rel=0.3)


def test_error_budget_inverts_the_fidelity_model():
    rows = error_budget(
        [
            {
                "case": "x",
                "qubits": 21,
                "epsilon": 1e-3,
                "qae_grover_depth": 64,
                "cx_loading": 100,
                "cx_per_grover_iterate": 1000,
            }
        ]
    )
    for row in rows:
        assert (1 - row["error_needed_for_half_fidelity"]) ** row[
            "two_qubit_gates"
        ] == pytest.approx(0.5)
