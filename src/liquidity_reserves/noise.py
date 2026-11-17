"""Amplitude estimation on hardware-realistic noise, and an error budget.

Part 1 runs a real amplitude-estimation circuit under a noise model built
from median IBM Heron r2 calibration figures (CZ error 3.2e-3, single-qubit
error 2.3e-4, readout error 1.0%, T1 197 us, T2 118 us; IBM fake-backend
snapshots, February 2025), scaled down to show what better hardware buys.
The circuit loads the public two-bond payout law on 6 qubits (5 payout
qubits and a flag) and is compiled to the Heron basis (cz, rz, sx, x). Aer's
density-matrix method gives the exact noisy flag probability; seeded shots
then feed two estimators:

- standard maximum-likelihood amplitude estimation (assumes no noise);
- noise-aware MLAE, which fits the amplitude and a depolarizing decay
  exp(-lambda (2k + 1)) together (Tanaka et al. 2021, Quantum Inf. Process.
  20, 293).

Part 2 projects the full tensor-chain circuits with their compiled CX counts:
the circuit fidelity after k Grover steps is about (1 - e)^(CX(A) + k CX(Q)),
which gives the two-qubit error rate each precision needs.
"""

from __future__ import annotations

import json
import math

import numpy as np

from .paths import results_dir
from .quantum import loaded_state, mlae

HERON_R2 = {
    "cz_error": 3.2e-3,
    "sx_error": 2.3e-4,
    "readout_error": 0.010,
    "t1_us": 197.0,
    "t2_us": 118.0,
    "cz_ns": 68.0,
    "sx_ns": 32.0,
    "source": "median of IBM Marrakesh (Heron r2) calibration snapshot, qiskit-ibm-runtime "
    "fake_provider, February 2025",
}
POWERS = (0, 1, 2, 4, 8)
SEED = 20261113


def noise_model(scale: float):
    """Heron r2-like noise with every error rate multiplied by scale (0 = ideal)."""
    from qiskit_aer.noise import (
        NoiseModel,
        ReadoutError,
        depolarizing_error,
        thermal_relaxation_error,
    )

    model = NoiseModel(basis_gates=["cz", "rz", "sx", "x", "id"])
    if scale <= 0:
        return model
    h = HERON_R2
    t1, t2 = h["t1_us"] * 1e-6 / scale, h["t2_us"] * 1e-6 / scale

    def relax(ns):
        return thermal_relaxation_error(t1, min(t2, 2 * t1), ns * 1e-9)

    one = depolarizing_error(h["sx_error"] * scale, 1).compose(relax(h["sx_ns"]))
    two = depolarizing_error(h["cz_error"] * scale, 2).compose(
        relax(h["cz_ns"]).tensor(relax(h["cz_ns"]))
    )
    model.add_all_qubit_quantum_error(one, ["sx", "x"])
    model.add_all_qubit_quantum_error(two, ["cz"])
    p = h["readout_error"] * scale
    model.add_all_qubit_readout_error(ReadoutError([[1 - p, p], [p, 1 - p]]))
    return model


def qae_circuits(curve, amount_units: int, powers=POWERS):
    """A and Q^k A circuits on payout qubits plus a flag, measuring the flag."""
    from qiskit import QuantumCircuit
    from qiskit.circuit.library import StatePreparation

    amplitudes, mask, width = loaded_state(curve, amount_units)
    size = len(amplitudes)
    state = np.zeros(2 * size)
    state[:size] = np.where(mask, 0.0, amplitudes)
    state[size:] = np.where(mask, amplitudes, 0.0)
    n = width + 1
    flag = width
    a = QuantumCircuit(n)
    a.append(StatePreparation(state), range(n))
    q = QuantumCircuit(n)
    q.z(flag)  # mark the flagged states
    q.compose(a.inverse(), inplace=True)
    q.x(range(n))
    q.h(n - 1)
    q.mcx(list(range(n - 1)), n - 1)
    q.h(n - 1)
    q.x(range(n))
    q.compose(a, inplace=True)
    circuits = []
    for k in powers:
        c = QuantumCircuit(n, 1)
        c.compose(a, inplace=True)
        for _ in range(k):
            c.compose(q, inplace=True)
        c.measure(flag, 0)
        circuits.append(c)
    return circuits, float(np.sum(amplitudes[mask] ** 2))


def flag_probabilities(circuits, scale: float):
    from qiskit import transpile
    from qiskit_aer import AerSimulator

    simulator = AerSimulator(method="density_matrix", noise_model=noise_model(scale))
    compiled = transpile(
        circuits,
        simulator,
        basis_gates=["cz", "rz", "sx", "x"],
        optimization_level=1,
        seed_transpiler=7,
    )
    counts = [int(c.count_ops().get("cz", 0)) for c in compiled]
    shots = 200_000
    result = simulator.run(compiled, shots=shots, seed_simulator=SEED).result()
    probs = [result.get_counts(i).get("1", 0) / shots for i in range(len(compiled))]
    return np.array(probs), counts


def noise_aware_mlae(hits, shots, powers, floors=None):
    """Joint maximum likelihood over the amplitude angle, a decay rate and the noise floor.

    The flag probability after k Grover steps is modelled as
    exp(-lambda (2k + 1)) sin^2((2k + 1) theta) + (1 - exp(-lambda (2k + 1))) b.
    Depolarizing noise alone gives b = 1/2; relaxation and readout error move
    it, so b is fitted on a grid as well.
    """
    hits, shots, m = (
        np.asarray(hits, float),
        np.asarray(shots, float),
        2 * np.asarray(powers, float) + 1,
    )
    thetas = np.linspace(1e-6, np.pi / 2 - 1e-6, 2001)
    lambdas = np.r_[0.0, np.geomspace(1e-4, 2.0, 60)]
    floors = np.linspace(0.0, 0.6, 13) if floors is None else np.atleast_1d(floors)
    sines = np.sin(m[None, :] * thetas[:, None]) ** 2
    best, arg = -np.inf, (0.0, 0.0, 0.5)
    for b in floors:
        for lam in lambdas:
            decay = np.exp(-lam * m)[None, :]
            p = np.clip(decay * sines + (1 - decay) * b, 1e-12, 1 - 1e-12)
            ll = (hits * np.log(p) + (shots - hits) * np.log(1 - p)).sum(axis=1)
            i = int(np.argmax(ll))
            if ll[i] > best:
                best, arg = ll[i], (thetas[i], lam, b)
    return float(np.sin(arg[0]) ** 2), float(arg[1])


def noise_study(scales=(0.0, 0.01, 0.1, 1.0), shots: int = 1000, repeats: int = 100) -> dict:
    from .bounds import bracket_reserve
    from .examples import DEFAULT_RANGE, public_two_bond

    result = bracket_reserve(public_two_bond(), DEFAULT_RANGE, 0.015)
    curve, amount = result.base.curve, result.base.lower_units
    circuits, truth = qae_circuits(curve, amount)
    rng = np.random.default_rng(SEED)
    rows = []
    for scale in scales:
        probs, cz = flag_probabilities(circuits, scale)
        ideal = np.sin((2 * np.array(POWERS) + 1) * math.asin(math.sqrt(truth))) ** 2
        plain, aware = [], []
        for _ in range(repeats):
            hits = rng.binomial(shots, probs)
            plain.append(mlae(hits, np.full(len(POWERS), shots), list(POWERS)))
            aware.append(noise_aware_mlae(hits, np.full(len(POWERS), shots), POWERS)[0])
        plain, aware = np.array(plain), np.array(aware)
        rows.append(
            {
                "error_scale": scale,
                "cz_error": HERON_R2["cz_error"] * scale,
                "cz_gates": dict(zip([str(k) for k in POWERS], cz, strict=True)),
                "flag_probability": dict(
                    zip([str(k) for k in POWERS], probs.tolist(), strict=True)
                ),
                "ideal_flag_probability": dict(
                    zip([str(k) for k in POWERS], ideal.tolist(), strict=True)
                ),
                "mlae_rmse": float(np.sqrt(np.mean((plain - truth) ** 2))),
                "noise_aware_mlae_rmse": float(np.sqrt(np.mean((aware - truth) ** 2))),
                "mlae_median": float(np.median(plain)),
                "noise_aware_median": float(np.median(aware)),
            }
        )
    return {
        "truth": truth,
        "powers": list(POWERS),
        "shots_per_power": shots,
        "repeats": repeats,
        "rows": rows,
    }


def error_budget(resources: list) -> list:
    """Two-qubit error rate at which the full circuit keeps fidelity 1/2 at each depth."""
    rows = []
    for r in resources:
        if r["epsilon"] != 1e-3:
            continue
        for depth in (16, r["qae_grover_depth"]):
            gates = r["cx_loading"] + depth * r["cx_per_grover_iterate"]
            needed = 1 - 2 ** (-1 / gates)
            heron = (1 - HERON_R2["cz_error"]) ** gates
            rows.append(
                {
                    "case": r["case"],
                    "qubits": r["qubits"],
                    "grover_depth": depth,
                    "two_qubit_gates": int(gates),
                    "error_needed_for_half_fidelity": needed,
                    "fidelity_at_heron_r2": heron,
                    "improvement_needed": HERON_R2["cz_error"] / needed,
                }
            )
    return rows


def main(argv=None) -> int:
    study = noise_study()
    quantum = json.loads((results_dir() / "quantum.json").read_text(encoding="utf-8"))
    budget = error_budget(quantum["resources"])
    out = results_dir() / "noise.json"
    out.write_text(
        json.dumps(
            {
                "schema": "dlr-noise-v1",
                "noise_model": HERON_R2,
                "simulator": "Qiskit Aer density-matrix method; circuits compiled to "
                "cz/rz/sx/x, optimization level 1",
                "study": study,
                "error_budget": budget,
                "reading": "Noise pulls the flag probability towards one half as Grover "
                "depth grows. The "
                "noise-aware estimator recovers much of the loss on the small circuit; the full "
                "tensor-chain circuits need error rates that only fault-tolerant devices offer.",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    for row in study["rows"]:
        print(
            f"scale {row['error_scale']:>5}: MLAE RMSE {row['mlae_rmse']:.2e}, noise-aware "
            f"{row['noise_aware_mlae_rmse']:.2e}, CZ at k=8 {row['cz_gates']['8']}"
        )
    for row in budget[:4]:
        print(
            f"{row['case']}, depth {row['grover_depth']}: "
            f"needs {row['error_needed_for_half_fidelity']:.1e}"
        )
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
