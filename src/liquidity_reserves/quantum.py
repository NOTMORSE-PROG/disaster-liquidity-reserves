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


def compiled_resources(curve: Curve, amount_units: int) -> dict:
    """Qubits, CX count and depth of the loading operator A and one Grover iterate Q.

    Requires Qiskit. Circuits compile to rz/sx/x/cx with all-to-all connectivity.
    """
    from qiskit import QuantumCircuit, transpile
    from qiskit.circuit.library import StatePreparation, UCRYGate

    amplitudes, mask, width = loaded_state(curve, amount_units)
    qubits = width + 1
    load = QuantumCircuit(qubits, name="A")
    load.append(StatePreparation(amplitudes), range(width))
    load.append(UCRYGate([math.pi if f else 0.0 for f in mask]), [width, *range(width)])
    flag = qubits - 1
    step = QuantumCircuit(qubits)
    step.z(flag)
    step.compose(load.inverse(), inplace=True)
    step.x(range(qubits))
    step.h(flag)
    step.mcx(list(range(flag)), flag)
    step.h(flag)
    step.x(range(qubits))
    step.compose(load, inplace=True)
    basis = ["rz", "sx", "x", "cx"]
    rows = {}
    for name, circuit in (("loading_A", load), ("grover_iterate_Q", step)):
        compiled = transpile(circuit, basis_gates=basis, optimization_level=1, seed_transpiler=7)
        rows[name] = {
            "qubits": compiled.num_qubits,
            "cx": int(compiled.count_ops().get("cx", 0)),
            "depth": int(compiled.depth()),
        }
    return {"qubits": qubits, "register_qubits": width, "circuits": rows}


def resource_table(cases, precisions=(1e-3, 1e-4)) -> list:
    """Resource rows for (label, curve, amount) cases; compiled counts need Qiskit."""
    rows = []
    for label, curve, amount in cases:
        compiled = compiled_resources(curve, amount)
        probability = curve.exceedance(amount)
        q = compiled["circuits"]["grover_iterate_Q"]
        for epsilon in precisions:
            need = queries_for_precision(probability, epsilon)
            rows.append(
                {
                    "case": label,
                    "probability": probability,
                    "qubits": compiled["qubits"],
                    "cx_loading": compiled["circuits"]["loading_A"]["cx"],
                    "cx_per_grover_iterate": q["cx"],
                    "depth_per_grover_iterate": q["depth"],
                    **need,
                    "cx_deepest_circuit": need["qae_grover_depth"] * q["cx"],
                }
            )
    return rows


def main(argv=None) -> int:
    """Write results/quantum.json: query study and compiled resources (needs Qiskit)."""
    from .bounds import bracket_reserve
    from .examples import DEFAULT_RANGE, public_two_bond, synthetic_chain
    from .paths import results_dir

    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)
    pool = public_two_bond()
    result = bracket_reserve(pool, DEFAULT_RANGE, 0.015)
    base = result.base.curve
    study = query_study(base, result.base.lower_units)
    cases = [("Public two-bond, base, P(L > $80m)", base, result.base.lower_units)]
    for n in (5, 10, 20):
        chain = synthetic_chain(n)
        chain_result = bracket_reserve(chain, DEFAULT_RANGE, 0.015, gap_usd=1_000_000)
        cases.append(
            (
                f"Synthetic {n} countries, base",
                chain_result.base.curve,
                chain_result.base.lower_units,
            )
        )
    report = {
        "schema": "dlr-quantum-v1",
        "scope": "Ideal noiseless statevector simulation on a CPU; no hardware, no speed-up claim.",
        "query_study": study,
        "resources": resource_table(cases),
        "compiler": "Qiskit transpile to rz/sx/x/cx, optimization level 1, all-to-all coupling",
    }
    output = args.output or results_dir() / "quantum.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(f"Quantum check written to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
