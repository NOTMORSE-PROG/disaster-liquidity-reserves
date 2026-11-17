"""QAOA for the risk-layering choice, written as a QUBO.

Each breakpoint (top of the cash reserve a1, top of the credit line a2) is
chosen from K candidate levels with a one-hot register, so the problem has 2K
binary variables:

    minimise  sum_i c1_i x_i + sum_j c2_j y_j
              + P (sum_i x_i - 1)^2 + P (sum_j y_j - 1)^2     one level each
              + P sum_{level_i > level_j} x_i y_j               reserve top below credit top
              [+ P sum_{level_j - level_i > cap} x_i y_j]       credit cap, if any

c1 and c2 are the a1-only and a2-only cost terms from layering.py, scaled to
[0, 1], so the QUBO's feasible optimum is the exact layering optimum on the
candidate levels. The QUBO maps to an Ising Hamiltonian (x = (1 - z) / 2).
QAOA (Farhi, Goldstone and Gutmann 2014) with the CVaR objective of
Barkoutsos et al. (2020, Quantum 4, 256) is trained on the exact simulated
state, then the trained circuit is sampled with seeded shots on Qiskit Aer.
Brute force over all 2^(2K) bitstrings checks the answer.

This is a demonstration of the formulation at a size simulators handle
exactly. The classical scan in layering.py stays the reference.
"""

from __future__ import annotations

import numpy as np

from .layering import CostParameters, _layer_costs, excess_curve, plan_cost
from .money import UNITS_PER_USD

SEED = 20261113


def candidate_levels(top: int, k: int) -> np.ndarray:
    """K distinct support indices spread evenly from 0 to top (inclusive)."""
    return np.unique(np.round(np.linspace(0, top, k)).astype(int))


def build_qubo(h1, h2, levels_usd, options, credit_limit_usd=None, penalty_scale=2.0):
    """Symmetric Q with energy x^T Q x (+ offset) for the one-hot layering choice."""
    k = len(options)
    c1 = np.asarray(h1)[options]
    c2 = np.asarray(h2)[options]
    shift = min(c1.min(), 0) + min(c2.min(), 0)
    spread = max(np.ptp(np.r_[c1, c2]), 1e-12)
    c1 = (c1 - c1.min()) / spread
    c2 = (c2 - c2.min()) / spread
    penalty = penalty_scale * (1.0 + c1.max() + c2.max())
    n = 2 * k
    q = np.zeros((n, n))
    for block, costs in ((0, c1), (k, c2)):
        # P (sum x - 1)^2 = P (sum x_i^2 + 2 sum_{i<j} x_i x_j - 2 sum x_i + 1), with x_i^2 = x_i
        for i in range(k):
            q[block + i, block + i] += costs[i] - penalty
            for j in range(i + 1, k):
                q[block + i, block + j] += penalty
                q[block + j, block + i] += penalty
    levels = np.asarray(levels_usd)[options]
    for i in range(k):
        for j in range(k):
            bad = levels[i] > levels[j] + 1e-6
            if credit_limit_usd is not None:
                bad = bad or levels[j] - levels[i] > credit_limit_usd + 1e-6
            if bad:
                q[i, k + j] += penalty / 2
                q[k + j, i] += penalty / 2
    offset = 2 * penalty
    return q, offset, {"penalty": penalty, "scale": spread, "shift": shift}


def energies(q: np.ndarray, offset: float) -> np.ndarray:
    """x^T Q x + offset for every bitstring, indexed little-endian (qubit 0 = bit 0)."""
    n = len(q)
    states = ((np.arange(2**n)[:, None] >> np.arange(n)) & 1).astype(float)
    return np.einsum("si,ij,sj->s", states, q, states) + offset


def qubo_to_ising(q: np.ndarray):
    """Ising operator H with x^T Q x = <H> + constant, using x = (1 - z) / 2."""
    from qiskit.quantum_info import SparsePauliOp

    n = len(q)
    linear = -q.sum(axis=1) / 2
    terms = [("Z", [i], linear[i]) for i in range(n)]
    terms += [
        ("ZZ", [i, j], q[i, j] / 2) for i in range(n) for j in range(i + 1, n) if q[i, j] != 0
    ]
    constant = np.trace(q) / 2 + (q.sum() - np.trace(q)) / 4
    return SparsePauliOp.from_sparse_list(terms, n).simplify(), float(constant)


def decode(index: int, k: int):
    bits = [(index >> b) & 1 for b in range(2 * k)]
    return bits[:k], bits[k:]


def _cvar(probabilities: np.ndarray, values: np.ndarray, alpha: float) -> float:
    order = np.argsort(values)
    p = probabilities[order]
    cum = np.cumsum(p)
    take = np.minimum(p, np.maximum(alpha - (cum - p), 0.0))
    return float(take @ values[order] / alpha)


def _xy_parts(n: int):
    """W-state start and ring XY mixer for two one-hot registers of n/2 qubits each.

    Each XX + YY term swaps excitations between neighbours, so the mixer keeps
    exactly one 1 in each register (Hadfield et al. 2019, Algorithms 12, 34).
    """
    from qiskit import QuantumCircuit
    from qiskit.circuit.library import StatePreparation
    from qiskit.quantum_info import SparsePauliOp

    k = n // 2
    w = np.zeros(2**k)
    w[[1 << i for i in range(k)]] = 1 / np.sqrt(k)
    start = QuantumCircuit(n)
    start.append(StatePreparation(w), range(k))
    start.append(StatePreparation(w), range(k, n))
    pairs = [(i, (i + 1) % k) for i in range(k if k > 2 else 1)]
    terms = []
    for block in (0, k):
        for i, j in pairs:
            terms += [("XX", [block + i, block + j], 0.5), ("YY", [block + i, block + j], 0.5)]
    return start, SparsePauliOp.from_sparse_list(terms, n)


def solve_qaoa(
    q: np.ndarray,
    offset: float,
    reps: int = 3,
    alpha: float = 0.25,
    shots: int = 4000,
    restarts: int = 4,
    maxiter: int = 400,
    seed: int = SEED,
    mixer: str = "xy",
) -> dict:
    """Train CVaR-QAOA on the exact state, then sample the trained circuit on Aer."""
    from qiskit import transpile
    from qiskit.circuit.library import qaoa_ansatz
    from qiskit.quantum_info import Statevector
    from qiskit_aer import AerSimulator
    from qiskit_aer.primitives import SamplerV2
    from scipy.optimize import minimize

    hamiltonian, constant = qubo_to_ising(q)
    values = energies(q, offset)
    if mixer == "xy":
        start, mixer_operator = _xy_parts(len(q))
        ansatz = qaoa_ansatz(
            hamiltonian, reps=reps, initial_state=start, mixer_operator=mixer_operator
        )
    elif mixer == "x":
        ansatz = qaoa_ansatz(hamiltonian, reps=reps)
    else:
        raise ValueError("mixer must be 'xy' or 'x'")
    # qaoa_ansatz orders parameters beta[0..p-1] then gamma[0..p-1]
    n_params = ansatz.num_parameters
    rng = np.random.default_rng(seed)
    cache = {}

    def probabilities(theta):
        key = tuple(np.round(theta, 12))
        if key not in cache:
            state = Statevector(ansatz.assign_parameters(theta))
            cache[key] = np.abs(state.data) ** 2
        return cache[key]

    def objective(theta):
        return _cvar(probabilities(theta), values, alpha)

    best = None
    for r in range(restarts):
        # linear-ramp start (a discretised annealing path), then random restarts
        if r == 0:
            ramp = (np.arange(reps) + 0.5) / reps
            theta0 = np.r_[0.6 * (1 - ramp), 0.6 * ramp]
        else:
            theta0 = rng.uniform(0, np.pi, n_params)
        fit = minimize(
            objective, theta0, method="COBYLA", options={"maxiter": maxiter, "rhobeg": 0.4}
        )
        if best is None or fit.fun < best.fun:
            best = fit
    exact_probs = probabilities(best.x)

    measured = ansatz.assign_parameters(best.x)
    measured.measure_all()
    simulator = AerSimulator(method="statevector")
    compiled = transpile(
        measured,
        simulator,
        basis_gates=["rz", "sx", "x", "cx"],
        optimization_level=1,
        seed_transpiler=7,
    )
    counts = (
        SamplerV2(seed=seed).run([(compiled, [])], shots=shots).result()[0].data.meas.get_counts()
    )
    sampled = np.zeros(len(values))
    for bitstring, c in counts.items():
        sampled[int(bitstring, 2)] = c / shots

    optimum = int(np.argmin(values))
    ops = compiled.count_ops()
    return {
        "qubits": len(q),
        "reps": reps,
        "parameters": n_params,
        "cvar_alpha": alpha,
        "objective_evaluations": len(cache),
        "most_frequent_index": int(np.argmax(sampled)),
        "most_frequent_is_optimal": bool(int(np.argmax(sampled)) == optimum),
        "shots": shots,
        "uniform_probability_optimal": 1.0 / len(values),
        "compiled_cx": int(ops.get("cx", 0)),
        "compiled_depth": int(compiled.depth()),
        "mixer": "XY ring (one-hot preserving)" if mixer == "xy" else "standard X",
        "simulator": "Qiskit Aer SamplerV2, seeded shots; training on the exact statevector",
        "energies": values,
        "sampled": sampled,
        "trained": exact_probs,
    }


def layering_qaoa(
    plan_inputs: dict,
    k: int = 6,
    reps: int = 3,
    alpha: float = 0.25,
    shots: int = 4000,
    credit_limit_usd: float | None = None,
    seed: int = SEED,
    mixer: str = "xy",
) -> dict:
    """Solve the discretised layering choice with QAOA and check it by brute force."""
    g, levels, top, term, cost = (
        plan_inputs[key] for key in ("g", "levels", "top", "term", "cost")
    )
    h1, h2 = _layer_costs(g, levels, top, term, cost)
    options = candidate_levels(top, k)
    k = len(options)
    q, offset, meta = build_qubo(h1, h2, levels, options, credit_limit_usd)
    values = energies(q, offset)

    def feasible_index(index):
        x, y = decode(index, k)
        if sum(x) != 1 or sum(y) != 1:
            return False
        i, j = x.index(1), y.index(1)
        if levels[options[i]] > levels[options[j]] + 1e-6:
            return False
        return not (
            credit_limit_usd is not None
            and levels[options[j]] - levels[options[i]] > credit_limit_usd + 1e-6
        )

    feasible = np.array([feasible_index(s) for s in range(len(values))])
    brute = int(np.flatnonzero(feasible)[np.argmin(values[feasible])])
    run = solve_qaoa(q, offset, reps=reps, alpha=alpha, shots=shots, seed=seed, mixer=mixer)
    sampled, trained = run.pop("sampled"), run.pop("trained")
    run.pop("energies")
    run["probability_feasible_sampled"] = float(sampled[feasible].sum())
    run["probability_feasible_trained_state"] = float(trained[feasible].sum())
    top_sample = int(np.argmax(np.where(feasible, sampled, -1)))
    x, y = decode(brute, k)
    a1, a2 = options[x.index(1)], options[y.index(1)]
    xs, ys = decode(top_sample, k)
    best_plan = plan_cost(g, levels, a1, a2, top, term, cost)
    sampled_plan = plan_cost(g, levels, options[xs.index(1)], options[ys.index(1)], top, term, cost)
    return {
        "candidate_levels_usd": [float(levels[i]) for i in options],
        "variables": 2 * k,
        "penalty": meta["penalty"],
        "brute_force_optimum": best_plan["layers_usd"],
        "brute_force_cost_usd": best_plan["total_cost_usd"],
        "most_frequent_feasible_sample": sampled_plan["layers_usd"],
        "most_frequent_feasible_cost_usd": sampled_plan["total_cost_usd"],
        "qaoa_finds_optimum": bool(top_sample == brute),
        **run,
        "probability_optimal_trained_state": float(trained[brute]),
        "probability_optimal_sampled": float(sampled[brute]),
        "optimal_index": brute,
    }


def plan_inputs(
    pool, shock_range, risk_limit, cost: CostParameters | None = None, basis="base", result=None
):
    """g(a), levels and the top index used by both the exact scan and QAOA."""
    from .bounds import bracket_reserve
    from .contraction import choose_registers

    cost = (cost or CostParameters()).validate()
    result = result or bracket_reserve(
        pool, shock_range, risk_limit, registers=choose_registers(pool)
    )
    if basis == "base":
        support, tail = result.base_upper_curve.support_units, result.base_upper_curve.tail
    else:
        support, tail = result.registers.upper.support_units, result.upper_envelope()
    top = int(np.searchsorted(support, result.upper_units, side="right")) - 1
    return {
        "g": excess_curve(support, tail),
        "levels": support.astype(float) / UNITS_PER_USD,
        "top": top,
        "term": float(pool.term_years),
        "cost": cost,
    }
