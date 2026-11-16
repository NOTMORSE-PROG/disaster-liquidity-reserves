import math

import numpy as np
import pytest

pytest.importorskip("qiskit")

from liquidity_reserves.contraction import choose_registers, distribution  # noqa: E402
from liquidity_reserves.money import usd_to_units  # noqa: E402
from liquidity_reserves.quantum_chain import (  # noqa: E402
    chain_circuit,
    check_arithmetic,
    decide,
    exact_joint_law,
    loaded_outcome_law,
    payout_levels,
)

M80 = usd_to_units(80_000_000)


@pytest.fixture(scope="module")
def base_circuit(two_bond):
    rates = two_bond.rates(two_bond.base_point)
    return chain_circuit(two_bond, rates, M80), rates


def test_circuit_layout_grows_by_eight_qubits_per_country(two_bond, base_circuit):
    chain, _ = base_circuit
    assert chain.num_qubits == 4 + 8 + len(chain.sum_qubits) + 1 == 21
    assert chain.threshold_ticks == 32  # $80m on the $2.5m payout lattice
    assert chain.weights == [4, 7, 14, 28, 4, 7, 14, 28]


def test_loaded_outcomes_carry_the_exact_joint_law(two_bond, base_circuit):
    chain, rates = base_circuit
    law = loaded_outcome_law(chain, 2)
    assert np.max(np.abs(law - exact_joint_law(two_bond, rates))) < 1e-12


def test_loaded_law_for_three_overlapping_countries():
    from liquidity_reserves.examples import synthetic_chain

    pool = synthetic_chain(3)
    rates = pool.rates((1.4, 0.2))
    chain = chain_circuit(pool, rates, usd_to_units(130_000_000))
    assert np.max(np.abs(loaded_outcome_law(chain, 3) - exact_joint_law(pool, rates))) < 1e-12


def test_payout_arithmetic_is_exact_for_every_outcome_pair(two_bond, base_circuit):
    chain, _ = base_circuit
    result = check_arithmetic(chain, 2)
    assert result["combinations"] == 100
    assert result["register_correct"] and result["flag_correct"]
    assert result["worst_basis_leakage"] < 1e-12


def test_full_circuit_matches_the_amplitude_rotation(two_bond, base_circuit):
    pytest.importorskip("qiskit_aer")
    from liquidity_reserves.quantum_chain import full_circuit_flag_probabilities

    chain, rates = base_circuit
    probability = distribution(two_bond, rates, choose_registers(two_bond).lower).exceedance(M80)
    theta = math.asin(math.sqrt(probability))
    observed = full_circuit_flag_probabilities(chain, [0, 1])
    expected = [math.sin((2 * k + 1) * theta) ** 2 for k in (0, 1)]
    assert np.max(np.abs(np.array(observed) - expected)) < 1e-9


def test_payout_levels_come_from_contract_terms(two_bond):
    levels = payout_levels(two_bond)
    assert levels[0] == 0 and levels[-1] == sum(c.principal_units for c in two_bond.countries)
    assert M80 in levels


def test_decision_rule():
    assert decide({"ci95": [0.010, 0.012]}, 0.015) == "meets"
    assert decide({"ci95": [0.016, 0.020]}, 0.015) == "exceeds"
    assert decide({"ci95": [0.014, 0.016]}, 0.015) == "ambiguous"


def test_confidence_region_covers_and_flags_inconsistency():
    from liquidity_reserves.quantum_chain import confidence_region

    powers = [0, 1, 2, 4, 8]
    truth = 0.0123
    theta = math.asin(math.sqrt(truth))
    shots = np.full(len(powers), 10_000)
    hits = np.rint(shots * np.sin((2 * np.array(powers) + 1) * theta) ** 2)
    low, high = confidence_region(hits, shots, powers)
    assert low <= truth <= high and high - low < 1e-3
    # Counts that no single amplitude can explain give no region at all.
    assert confidence_region([0, 10_000], [10_000, 10_000], [0, 1]) is None


def test_decision_treats_missing_region_as_ambiguous():
    assert decide({"ci95": None}, 0.015) == "ambiguous"
