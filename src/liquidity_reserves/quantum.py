"""Amplitude-estimation check of an exceedance probability (ideal simulator).

This is the Phase 2 quantum path, run here as a noiseless statevector
simulation on the CPU. A payout register holds the aggregate payout law from
the tensor contraction, a flag qubit marks payouts strictly above the reserve,
and Grover powers amplify the flag. Maximum-likelihood amplitude estimation
(MLAE) turns finite-shot flag counts into an estimate of P(L > R).

Scope, stated plainly: loading the law from a classical contraction costs at
least as much as reading P(L > R) from it, so this shows no end-to-end
speed-up. It checks the estimator, its query scaling against Monte Carlo at the
same number of model queries, and the circuit resources a hardware run would
need. Compiled gate counts use Qiskit when it is installed (optional extra).
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
from scipy.optimize import minimize_scalar
from scipy.special import xlog1py, xlogy

from .curve import Curve

Z95 = 1.959963984540054


def loaded_state(curve: Curve, amount_units: int):
    """Amplitudes over attained payout levels, padded to 2^n, and the flag mask."""
    attained = curve.probabilities > 0
    probabilities = curve.probabilities[attained] / curve.probabilities[attained].sum()
    flag = curve.support_units[attained] > amount_units
    width = max(1, math.ceil(math.log2(len(probabilities))))
    amplitudes = np.zeros(1 << width)
    amplitudes[: len(probabilities)] = np.sqrt(probabilities)
    mask = np.zeros(1 << width, dtype=bool)
    mask[: len(flag)] = flag
    return amplitudes, mask, width


def grover_probabilities(amplitudes: np.ndarray, mask: np.ndarray, powers) -> np.ndarray:
    """Flag probability after k Grover iterates, by statevector evolution.

    The full state has the payout register and a flag qubit; the flag is set
    exactly on payouts above the reserve, so the state is block structured and
    is simulated directly on its 2^(n+1) amplitudes.
    """
    size = len(amplitudes)
    prepared = np.zeros(2 * size)
    prepared[:size] = np.where(mask, 0.0, amplitudes)  # flag 0
    prepared[size:] = np.where(mask, amplitudes, 0.0)  # flag 1
    state = prepared.copy()
    results, depth = {}, 0
    for power in sorted(set(int(p) for p in powers)):
        for _ in range(depth, power):
            state[size:] *= -1  # mark flagged states
            state = 2 * (prepared @ state) * prepared - state  # reflect about A|0>
        depth = power
        results[power] = float(np.sum(state[size:] ** 2))
    return np.array([results[int(p)] for p in powers])


def mlae(hits, shots, powers) -> float:
    """Maximum-likelihood amplitude from flag counts at several Grover powers."""
    hits, shots = np.asarray(hits), np.asarray(shots)
    frequencies = 2 * np.asarray(powers) + 1
    if not np.any(hits):
        return 0.0
    if np.all(hits == shots):
        return 1.0

    def loglik(theta):
        p = np.clip(np.sin(np.asarray(theta)[..., None] * frequencies) ** 2, 1e-15, 1 - 1e-15)
        return np.sum(xlogy(hits, p) + xlog1py(shots - hits, -p), axis=-1)

    # The likelihood oscillates with the highest Grover frequency; sample each
    # period finely so the global maximum is not missed between grid points.
    grid = np.linspace(0, np.pi / 2, max(4097, 64 * int(frequencies.max()) + 1))
    best = int(np.argmax(loglik(grid)))
    low, high = grid[max(0, best - 1)], grid[min(len(grid) - 1, best + 1)]
    refined = minimize_scalar(
        lambda t: -loglik(t), bounds=(low, high), method="bounded", options={"xatol": 1e-12}
    ).x
    theta = max((refined, grid[best]), key=lambda t: float(loglik(t)))
    return float(np.sin(theta) ** 2)


def query_study(
    curve: Curve,
    amount_units: int,
    schedules: int = 9,
    shots: int = 100,
    repeats: int = 200,
    seed: int = 20261113,
) -> dict:
    """RMSE of MLAE vs Monte Carlo at equal model queries.

    Schedule J uses Grover powers 0, 1, 2, 4, ..., 2^(J-1) with `shots` shots
    each; one shot at power k costs 2k + 1 queries of the loading operator.
    Monte Carlo with the same number of samples has RMSE sqrt(p(1-p)/n).
    With too few shots per power, MLAE occasionally locks onto a neighbouring
    likelihood peak; 100 shots per power keeps those failures out of the RMSE.
    """
    amplitudes, mask, width = loaded_state(curve, amount_units)
    truth = float(np.sum(amplitudes[mask] ** 2))
    rng = np.random.default_rng(seed)
    rows = []
    worst_check = 0.0
    for j in range(schedules + 1):
        powers = [0] + [2**k for k in range(j)]
        probabilities = grover_probabilities(amplitudes, mask, powers)
        theta = math.asin(math.sqrt(truth))
        analytic = np.sin((2 * np.asarray(powers) + 1) * theta) ** 2
        worst_check = max(worst_check, float(np.max(np.abs(probabilities - analytic))))
        counts = np.full(len(powers), shots)
        queries = int(counts @ (2 * np.asarray(powers) + 1))
        estimates = np.array(
            [mlae(rng.binomial(counts, probabilities), counts, powers) for _ in range(repeats)]
        )
        rows.append(
            {
                "max_power": powers[-1],
                "queries": queries,
                "qae_rmse": float(np.sqrt(np.mean((estimates - truth) ** 2))),
                "qae_median_abs_error": float(np.median(np.abs(estimates - truth))),
                "monte_carlo_rmse": math.sqrt(truth * (1 - truth) / queries),
            }
        )
    return {
        "probability": truth,
        "amount_usd": amount_units / 400,
        "register_qubits": width,
        "qubits": width + 1,
        "attained_levels": int(np.count_nonzero(curve.probabilities > 0)),
        "shots_per_power": shots,
        "repeats": repeats,
        "seed": seed,
        "statevector_vs_analytic_max_error": worst_check,
        "rows": rows,
    }


def queries_for_precision(probability: float, epsilon: float) -> dict:
    """Model queries for a 95% half-width epsilon: canonical QAE vs Monte Carlo.

    Canonical QAE with M queries has error at most
    2 pi sqrt(a(1-a)) / M + pi^2 / M^2 with probability at least 8/pi^2
    (Brassard et al. 2002); nine independent runs and a median lift that above
    95%. Monte Carlo needs z^2 a(1-a) / epsilon^2 samples.
    """
    a = probability
    spread = 2 * math.pi * math.sqrt(a * (1 - a))
    # Smallest M with spread/M + pi^2/M^2 <= epsilon.
    m = (spread + math.sqrt(spread**2 + 4 * epsilon * math.pi**2)) / (2 * epsilon)
    m = 2 ** math.ceil(math.log2(m))
    return {
        "epsilon": epsilon,
        "qae_grover_depth": m,
        "qae_queries_95": 9 * m,
        "monte_carlo_queries_95": math.ceil(Z95**2 * a * (1 - a) / epsilon**2),
    }


def interval_coverage(probability: float, repeats: int = 1000, seed: int = 20261113) -> list:
    """How often the confidence region covers the exact value, by repeated sampling.

    Shots are drawn from sin^2((2k + 1) theta), the flag probabilities the full
    circuit was verified to reproduce, so each repeat is a fresh run of the same
    estimator without re-simulating the circuit.
    """
    from .quantum_chain import confidence_region

    rng = np.random.default_rng(seed)
    theta = math.asin(math.sqrt(probability))
    rows = []
    for powers in ((0, 1, 2, 4, 8, 16), (0, 1, 2, 4, 8, 16, 32)):
        frequencies = 2 * np.asarray(powers) + 1
        p = np.sin(frequencies * theta) ** 2
        shots = np.full(len(powers), 100)
        covered, widths = 0, []
        for _ in range(repeats):
            region = confidence_region(rng.binomial(shots, p), shots, powers)
            if region is not None:
                covered += region[0] <= probability <= region[1]
                widths.append(region[1] - region[0])
        rows.append(
            {
                "max_power": powers[-1],
                "repeats": repeats,
                "coverage": covered / repeats,
                "median_width": float(np.median(widths)),
                "nominal": 0.95,
            }
        )
    return rows


def chain_study(seed: int = 20261113, log=print) -> dict:
    """Verify and run the tensor-chain circuit (needs qiskit and qiskit-aer)."""
    from .bounds import bracket_reserve
    from .contraction import choose_registers, distribution
    from .examples import DEFAULT_RANGE, public_two_bond, synthetic_chain
    from .quantum_chain import (
        FullCircuitEstimator,
        chain_circuit,
        check_arithmetic,
        compiled_counts,
        exact_joint_law,
        full_circuit_flag_probabilities,
        loaded_outcome_law,
        quantum_reserve_search,
    )

    pool = public_two_bond()
    rates = pool.rates(pool.base_point)
    classical = bracket_reserve(pool, DEFAULT_RANGE, 0.015)
    reserve = classical.base.lower_units
    probability = classical.base.curve.exceedance(reserve)
    two = chain_circuit(pool, rates, reserve)
    log("Verifying the two-country circuit")
    powers = [0, 1, 2, 3, 5]
    full = full_circuit_flag_probabilities(two, powers)
    theta = math.asin(math.sqrt(probability))
    theory = [math.sin((2 * k + 1) * theta) ** 2 for k in powers]
    three_pool = synthetic_chain(3)
    three_rates = three_pool.rates((1.4, 0.2))
    three = chain_circuit(three_pool, three_rates, 130_000_000 * 400)
    log("Verifying the three-country circuit")
    verification = {
        "two_countries": {
            "qubits": two.num_qubits,
            "loading_max_error": float(
                np.max(np.abs(loaded_outcome_law(two, 2) - exact_joint_law(pool, rates)))
            ),
            "arithmetic": check_arithmetic(two, 2),
            "full_circuit_powers": powers,
            "full_circuit_probabilities": full,
            "theory": theory,
            "full_circuit_max_error": float(
                max(abs(a - b) for a, b in zip(full, theory, strict=True))
            ),
        },
        "three_countries": {
            "qubits": three.num_qubits,
            "loading_max_error": float(
                np.max(
                    np.abs(loaded_outcome_law(three, 3) - exact_joint_law(three_pool, three_rates))
                )
            ),
            "arithmetic": check_arithmetic(three, 3),
        },
    }
    estimator = FullCircuitEstimator(shots=100, seed=seed)
    log("Full-circuit amplitude estimation at the base reserve")
    estimate = {**estimator.estimate(pool, rates, reserve), "exact": probability}
    searches = []
    for limit, expected in ((0.015, classical.base.lower_units), (0.005, None)):
        log(f"Quantum reserve search at {limit:.1%}")
        found = quantum_reserve_search(pool, pool.base_point, limit, estimator)
        exact_units = expected or bracket_reserve(pool, DEFAULT_RANGE, limit).base.lower_units
        searches.append({**found, "classical_reserve_usd": exact_units / 400})
    log("Certifying the range bounds at 1.5%")
    smaller, _ = classical.shortfall_evidence()
    deeper = (0, 1, 2, 4, 8, 16, 32)
    witness = estimator.estimate(pool, pool.rates(classical.witness.point), smaller, deeper)
    witness.update(
        point=list(classical.witness.point),
        exact=classical.witness.curve.exceedance(smaller),
        verdict="exceeds" if witness["ci95"] and witness["ci95"][0] > 0.015 else "not shown",
    )
    cells = []
    for cell in classical.leaves:
        bound = estimator.estimate(
            pool, pool.dominating_rates(cell.low, cell.high), classical.upper_units, deeper
        )
        bound.update(
            cell={
                "earthquake_multiplier": [float(cell.low[0]), float(cell.high[0])],
                "rain_share": [float(cell.low[1]), float(cell.high[1])],
            },
            exact=cell.curve.exceedance(classical.upper_units),
            verdict="meets" if bound["ci95"] and bound["ci95"][1] <= 0.015 else "not shown",
        )
        cells.append(bound)
    certification = {
        "risk_limit": 0.015,
        "lower_usd": classical.lower_units / 400,
        "upper_usd": classical.upper_units / 400,
        "witness": witness,
        "dominating_cells": cells,
        "certified": witness["verdict"] == "exceeds"
        and all(c["verdict"] == "meets" for c in cells),
    }
    log("Compiling resource counts")
    resources = []
    cases = [("Public two-bond", pool)] + [
        (f"Synthetic {n} countries", synthetic_chain(n)) for n in (2, 3, 5, 10, 20, 50)
    ]
    for label, chain_pool in cases:
        n = chain_pool.n
        base_rates = chain_pool.rates(chain_pool.base_point)
        curve = distribution(chain_pool, base_rates, choose_registers(chain_pool).lower)
        amount = curve.reserve_units(0.015)
        counts = compiled_counts(chain_circuit(chain_pool, base_rates, amount))
        q = counts["grover_iterate_Q"]
        for epsilon in (1e-3, 1e-4):
            need = queries_for_precision(curve.exceedance(amount), epsilon)
            resources.append(
                {
                    "countries": n,
                    "case": label,
                    "probability": curve.exceedance(amount),
                    "reserve_usd": amount / 400,
                    "qubits": q["qubits"],
                    "cx_loading": counts["loading_A"]["cx"],
                    "cx_per_grover_iterate": q["cx"],
                    "depth_per_grover_iterate": q["depth"],
                    **need,
                    "cx_deepest_circuit": need["qae_grover_depth"] * q["cx"],
                }
            )
    log("Measuring interval coverage")
    coverage = interval_coverage(probability)
    return {
        "verification": verification,
        "interval_coverage": coverage,
        "full_circuit_estimate": estimate,
        "reserve_search": searches,
        "bound_certification": certification,
        "resources": resources,
        "simulator": "Qiskit Aer 0.17.2 statevector, every qubit simulated, seeded shots",
        "compiler": "Qiskit 2.2.3 transpile to rz/sx/x/cx, optimization level 1, seed 7, "
        "all-to-all connectivity",
    }


def main(argv=None) -> int:
    """Write results/quantum.json (needs qiskit and qiskit-aer)."""
    from .bounds import bracket_reserve
    from .examples import DEFAULT_RANGE, public_two_bond
    from .paths import results_dir

    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)
    pool = public_two_bond()
    result = bracket_reserve(pool, DEFAULT_RANGE, 0.015)
    report = {
        "schema": "dlr-quantum-v2",
        "scope": "Simulated, noiseless quantum circuits on a CPU. No quantum hardware was used and "
        "no speed-up over the exact classical contraction is claimed.",
        "query_study": query_study(result.base.curve, result.base.lower_units),
        **chain_study(log=lambda message: print(message, flush=True)),
    }
    output = args.output or results_dir() / "quantum.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(f"Quantum study written to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
