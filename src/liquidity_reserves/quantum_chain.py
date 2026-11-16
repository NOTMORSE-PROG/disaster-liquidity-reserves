"""Quantum algorithm for the shortfall probability, loaded from the country tensors.

The loading operator A prepares the regional model directly from the
per-country tensors used by the classical contraction; no aggregate payout
law is computed in advance:

1. each neighbouring pair gets a 4-qubit register holding its shared state
   (shared earthquake count 0..3 or >=4, and whether a shared rain event
   occurred), prepared with amplitudes sqrt(w_pair);
2. each country gets a 4-qubit outcome register (bit 0: rain trigger, bits
   1-3: earthquake quarter steps), prepared with amplitudes
   sqrt(A_i(outcome | left pair, right pair));
3. a Fourier-space (Draper) adder accumulates every country's payout, in
   payout-lattice ticks, and subtracts the reserve plus one tick, all in one
   register with one spare bit and no ancilla qubits;
4. the register's sign bit is copied, inverted, into a flag qubit, so the
   flag is 1 exactly when total payouts are strictly above the reserve.

The pair registers purify the shared shocks, so the outcome registers carry
exactly the regional joint law and P(flag = 1) = P(L > R). Amplitude
estimation reads that probability. Qubits grow linearly with the number of
countries, because each conditional preparation touches one country and its
two pairs.

Everything here runs on simulators. The loading is checked against the exact
joint law, the arithmetic bit by bit for every outcome combination, and the
whole circuit by statevector simulation for two countries.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .contraction import LatticeRegister, choose_registers, local_tensors
from .portfolio import Rates, RegionalPool

REGISTER = 4  # 10 shared states or 10 outcomes, padded to 16


@dataclass
class ChainCircuit:
    load: object  # qiskit circuit on the pair and country qubits only
    circuit: object  # full loading operator A (load, weighted sum, comparator)
    pair_qubits: list
    country_qubits: list
    sum_qubits: list
    flag_qubit: int
    tick_units: int
    threshold_ticks: int
    weights: list

    @property
    def num_qubits(self) -> int:
        return self.circuit.num_qubits


def _conditional_rows(local: np.ndarray, position: str) -> np.ndarray:
    """P(outcome | controls) as rows indexed by the control register value.

    Control values outside the 10 valid shared states never carry amplitude;
    they get a fixed outcome so the multiplexor is well defined.
    """
    table = np.zeros((16, 16, 16))
    table[:, :, 0] = 1.0
    for a in range(local.shape[0]):
        for b in range(local.shape[1]):
            table[a, b, :10] = local[a, b] / local[a, b].sum()
            table[a, b, 10:] = 0.0
    if position == "only":
        return table[0:1, 0, :]
    if position == "first":
        return table[0, :, :]  # controls: right pair
    if position == "last":
        return table[:, 0, :]  # controls: left pair
    # controls: left pair (low bits) then right pair, so index = left + 16 * right
    return table.transpose(1, 0, 2).reshape(256, 16)


def _multiplexed_preparation(circuit, target, controls, rows):
    """Prepare sqrt(rows[c, x]) on `target` for each value c of `controls`.

    Bits are set from the most significant down. Each step is a uniformly
    controlled RY whose controls are the condition register plus the bits
    already set; Qiskit indexes its angles with the first control as the
    least significant bit.
    """
    from qiskit.circuit.library import UCRYGate

    width = len(target)
    n_controls = len(controls)
    values = np.arange(16)
    for j in reversed(range(width)):
        higher = list(range(j + 1, width))
        angles = []
        for index in range(2 ** (n_controls + len(higher))):
            c = index & ((1 << n_controls) - 1)
            prefix = index >> n_controls
            mask = np.ones(16, dtype=bool)
            for k, pos in enumerate(higher):
                mask &= ((values >> pos) & 1) == ((prefix >> k) & 1)
            total = rows[c][mask].sum()
            ones = rows[c][mask & (((values >> j) & 1) == 1)].sum()
            p1 = 0.0 if total <= 0 else min(max(ones / total, 0.0), 1.0)
            angles.append(2 * math.asin(math.sqrt(p1)))
        if max(abs(a) for a in angles) < 1e-15:
            continue
        circuit.append(UCRYGate(angles), [target[j], *controls, *[target[h] for h in higher]])


def _chunks(weights):
    return [weights[i : i + REGISTER] for i in range(0, len(weights), REGISTER)]


def _phase_add(circuit, register, value, control=None):
    """Add `value` (mod 2^len(register)) to a register held in the Fourier basis."""
    size = 2 ** len(register)
    for j, qubit in enumerate(register):
        angle = math.remainder(2 * math.pi * value * (2**j) / size, 2 * math.pi)
        if abs(angle) < 1e-15:
            continue
        if control is None:
            circuit.p(angle, qubit)
        else:
            circuit.cp(angle, control, qubit)


def _payout_comparison(circuit, register, flag, controls, threshold):
    """Register <- sum(weights of set bits) - (threshold + 1); flag <- sum > threshold.

    The register has one more bit than the largest possible sum, so the top
    bit is the sign of (sum - threshold - 1) in two's complement.
    """
    from qiskit.circuit.library import QFTGate

    width = len(register)
    circuit.append(QFTGate(width), register)
    for qubit, weight in controls:
        _phase_add(circuit, register, weight, control=qubit)
    _phase_add(circuit, register, -(threshold + 1))
    circuit.append(QFTGate(width).inverse(), register)
    sign = register[-1]
    circuit.x(sign)
    circuit.cx(sign, flag)
    circuit.x(sign)


def payout_weights(pool: RegionalPool, register: LatticeRegister) -> list:
    """Integer tick weight of each outcome bit: rain, then 1, 2 and 4 quarter steps."""
    step = register.step_units
    weights = []
    for country in pool.countries:
        quarter = country.earthquake_cap_units // 4
        if quarter % step or country.rain_cap_units % step:
            raise ValueError("payouts must lie on the exact payout lattice")
        q = quarter // step
        weights += [country.rain_cap_units // step, q, 2 * q, 4 * q]
    return weights


def chain_circuit(pool: RegionalPool, rates: Rates, reserve_units: int) -> ChainCircuit:
    """Loading operator A for one assumption and one reserve amount."""
    from qiskit import QuantumCircuit
    from qiskit.circuit.library import StatePreparation

    registers = choose_registers(pool)
    register = registers.lower
    if not registers.exact or not isinstance(register, LatticeRegister):
        raise ValueError("the quantum circuit needs exact payouts on a lattice")
    tensors, pair_weights = local_tensors(pool, rates)
    n = pool.n
    pair_qubits = [list(range(REGISTER * k, REGISTER * (k + 1))) for k in range(n - 1)]
    base = REGISTER * (n - 1)
    country_qubits = [list(range(base + REGISTER * i, base + REGISTER * (i + 1))) for i in range(n)]
    load = QuantumCircuit(REGISTER * (2 * n - 1), name="load")
    for k, weight in enumerate(pair_weights):
        amplitudes = np.zeros(16)
        amplitudes[:10] = np.sqrt(weight / weight.sum())
        load.append(StatePreparation(amplitudes), pair_qubits[k])
    for i, local in enumerate(tensors):
        if n == 1:
            position, controls = "only", []
        elif i == 0:
            position, controls = "first", pair_qubits[0]
        elif i == n - 1:
            position, controls = "last", pair_qubits[n - 2]
        else:
            position, controls = "middle", pair_qubits[i - 1] + pair_qubits[i]
        _multiplexed_preparation(
            load, country_qubits[i], controls, _conditional_rows(local, position)
        )

    weights = payout_weights(pool, register)
    threshold = reserve_units // register.step_units
    width = max(1, sum(weights).bit_length()) + 1  # one spare bit holds the sign
    sum_qubits = list(range(load.num_qubits, load.num_qubits + width))
    flag = sum_qubits[-1] + 1
    circuit = QuantumCircuit(flag + 1, name="A")
    circuit.compose(load, qubits=range(load.num_qubits), inplace=True)
    controls = [
        (q, w)
        for qubits, w4 in zip(country_qubits, _chunks(weights), strict=True)
        for q, w in zip(qubits, w4, strict=True)
    ]
    _payout_comparison(circuit, sum_qubits, flag, controls, threshold)
    return ChainCircuit(
        load,
        circuit,
        pair_qubits,
        country_qubits,
        sum_qubits,
        flag,
        register.step_units,
        threshold,
        weights,
    )


def grover_iterate(chain: ChainCircuit):
    """Q = A S0 A^dagger S_flag (up to a global phase)."""
    from qiskit import QuantumCircuit

    a = chain.circuit
    n = a.num_qubits
    step = QuantumCircuit(n, name="Q")
    step.z(chain.flag_qubit)
    step.compose(a.inverse(), inplace=True)
    step.x(range(n))
    step.h(n - 1)
    step.mcx(list(range(n - 1)), n - 1)
    step.h(n - 1)
    step.x(range(n))
    step.compose(a, inplace=True)
    return step


# ---------------------------------------------------------------------------
# Verification


def exact_joint_law(pool: RegionalPool, rates: Rates) -> np.ndarray:
    """Joint law of all country outcomes by direct tensor summation (small pools)."""
    tensors, weights = local_tensors(pool, rates)
    law = np.ones((1, 1))  # rows: outcomes so far (flattened); columns: open bond
    for i, local in enumerate(tensors):
        combined = np.einsum("pa,abo->pob", law, local)
        if i < len(weights):
            combined = combined * weights[i][None, None, :]
        law = combined.reshape(-1, local.shape[1])
    return law[:, 0].reshape((10,) * pool.n)


def loaded_outcome_law(chain: ChainCircuit, n: int) -> np.ndarray:
    """Outcome-register marginal of the loading circuit, by statevector simulation."""
    from qiskit.quantum_info import Statevector

    probabilities = Statevector(chain.load).probabilities()
    index = np.arange(len(probabilities))
    marginal = np.zeros((16,) * n)
    values = []
    for qubits in chain.country_qubits:
        values.append(sum(((index >> q) & 1) << b for b, q in enumerate(qubits)))
    np.add.at(marginal, tuple(values), probabilities)
    return marginal[(slice(0, 10),) * n]


def check_arithmetic(chain: ChainCircuit, n: int) -> dict:
    """Replay the payout arithmetic for every outcome combination.

    For each combination the outcome bits are classical, so the same phase
    additions act on the register and flag alone; the result must be the
    exact basis state sum - threshold - 1 (mod 2^width) with the right flag.
    """
    from qiskit import QuantumCircuit, transpile
    from qiskit.quantum_info import Statevector

    width = len(chain.sum_qubits)
    outcomes = list(np.ndindex(*(10,) * n))
    pay = np.array(chain.weights).reshape(n, REGISTER)
    worst, sums_ok, flags_ok = 0.0, True, True
    for combo in outcomes:
        active = []
        for i, value in enumerate(combo):
            active += [int(pay[i, b]) for b in range(REGISTER) if (value >> b) & 1]
        small = QuantumCircuit(width + 1)
        register = list(range(width))
        _payout_comparison(
            small, register, width, [(None, w) for w in active], chain.threshold_ticks
        )
        # Expand the Fourier transforms into one- and two-qubit gates; a dense
        # transform matrix makes this loop far too slow for three countries.
        small = transpile(
            small, basis_gates=["h", "p", "cp", "cx", "x", "swap"], optimization_level=0
        )
        probabilities = Statevector(small).probabilities()
        index = int(np.argmax(probabilities))
        worst = max(worst, 1 - float(probabilities[index]))
        total = sum(active)
        expected = (total - chain.threshold_ticks - 1) % (2**width)
        sums_ok &= (index & (2**width - 1)) == expected
        flags_ok &= bool((index >> width) & 1) == (total > chain.threshold_ticks)
    return {
        "combinations": len(outcomes),
        "register_correct": bool(sums_ok),
        "flag_correct": bool(flags_ok),
        "worst_basis_leakage": worst,
    }


def _compiled_pair(chain: ChainCircuit):
    """A and Q compiled to u/cx.

    Aer is given basic gates only: passing it the high-level multiplexors
    directly crashes the simulator process.
    """
    from qiskit import transpile

    basis = ["u", "cx"]
    a = transpile(chain.circuit, basis_gates=basis, optimization_level=1, seed_transpiler=7)
    q = transpile(grover_iterate(chain), basis_gates=basis, optimization_level=1, seed_transpiler=7)
    return a, q


def full_circuit_flag_probabilities(chain: ChainCircuit, powers) -> list:
    """P(flag = 1) after k Grover iterates, simulating every qubit (Aer statevector)."""
    from qiskit import QuantumCircuit
    from qiskit_aer import AerSimulator

    simulator = AerSimulator(method="statevector")
    a, q = _compiled_pair(chain)
    results = []
    for k in powers:
        circuit = QuantumCircuit(chain.num_qubits)
        circuit.compose(a, inplace=True)
        for _ in range(k):
            circuit.compose(q, inplace=True)
        circuit.save_probabilities([chain.flag_qubit])
        data = simulator.run(circuit).result().data(0)
        results.append(float(data["probabilities"][1]))
    return results


def compiled_counts(chain: ChainCircuit) -> dict:
    """Qubits, CX count and depth of A and of one Grover iterate, compiled to rz/sx/x/cx."""
    from qiskit import transpile

    rows = {}
    for name, circuit in (
        ("loading_A", chain.circuit),
        ("grover_iterate_Q", grover_iterate(chain)),
    ):
        compiled = transpile(
            circuit, basis_gates=["rz", "sx", "x", "cx"], optimization_level=1, seed_transpiler=7
        )
        rows[name] = {
            "qubits": compiled.num_qubits,
            "cx": int(compiled.count_ops().get("cx", 0)),
            "depth": int(compiled.depth()),
        }
    return rows


# ---------------------------------------------------------------------------
# Amplitude estimation on the full circuit, reserve search and certification

DEFAULT_POWERS = (0, 1, 2, 4, 8, 16)


def confidence_region(hits, shots, powers, alpha: float = 0.05):
    """Interval for P(flag = 1) with coverage of at least 1 - alpha by construction.

    Each Grover power gives an exact Clopper-Pearson interval for its flag
    probability at level alpha / (number of powers); keeping only the angles
    consistent with every interval at once gives a confidence set for the
    amplitude by the union bound. No large-sample approximation is used, so
    aliased (wrong-peak) fits cannot produce an overconfident interval. Returns
    None when no angle fits every interval, which the union bound counts as
    a miss.
    """
    from scipy.stats import beta

    frequencies = 2 * np.asarray(powers) + 1
    grid = np.linspace(0, np.pi / 2, 400 * int(frequencies.max()) + 1)
    allowed = np.ones(len(grid), dtype=bool)
    level = alpha / len(powers)
    for h, n, f in zip(hits, shots, frequencies, strict=True):
        h, n = int(h), int(n)
        low = 0.0 if h == 0 else float(beta.ppf(level / 2, h, n - h + 1))
        high = 1.0 if h == n else float(beta.ppf(1 - level / 2, h + 1, n - h))
        p = np.sin(f * grid) ** 2
        allowed &= (p >= low - 1e-15) & (p <= high + 1e-15)
    if not allowed.any():
        return None
    return float(np.sin(grid[allowed].min()) ** 2), float(np.sin(grid[allowed].max()) ** 2)


class FullCircuitEstimator:
    """Runs A Q^k on Aer's statevector simulator with shots and fits MLAE."""

    def __init__(self, shots: int = 100, seed: int = 20261113):
        from qiskit_aer import AerSimulator

        self.simulator = AerSimulator(method="statevector")
        self.shots = shots
        self.seed = seed
        self.runs = 0

    def estimate(self, pool: RegionalPool, rates: Rates, reserve_units: int, powers=DEFAULT_POWERS):
        from qiskit import QuantumCircuit

        from .quantum import mlae

        chain = chain_circuit(pool, rates, reserve_units)
        a, q = _compiled_pair(chain)
        hits = []
        for k in powers:
            circuit = QuantumCircuit(chain.num_qubits, 1)
            circuit.compose(a, inplace=True)
            for _ in range(k):
                circuit.compose(q, inplace=True)
            circuit.measure(chain.flag_qubit, 0)
            self.runs += 1
            counts = (
                self.simulator.run(circuit, shots=self.shots, seed_simulator=self.seed + self.runs)
                .result()
                .get_counts()
            )
            hits.append(int(counts.get("1", 0)))
        shots = np.full(len(powers), self.shots)
        estimate = mlae(hits, shots, list(powers))
        region = confidence_region(hits, shots, powers)
        return {
            "reserve_usd": reserve_units / 400,
            "estimate": estimate,
            "ci95": list(region) if region else None,
            "powers": list(powers),
            "shots_per_power": self.shots,
            "hits": hits,
            "queries": int(shots @ (2 * np.asarray(powers) + 1)),
            "qubits": chain.num_qubits,
        }


def payout_levels(pool: RegionalPool) -> np.ndarray:
    """Every attainable total payout (from the contract terms alone, no probabilities)."""
    totals = np.zeros(1, dtype=np.int64)
    for row in pool.payout_table():
        totals = np.unique(totals[:, None] + row[None, :])
    return totals


def decide(result: dict, risk_limit: float) -> str:
    if result["ci95"] is None:
        return "ambiguous"
    low, high = result["ci95"]
    if high <= risk_limit:
        return "meets"
    if low > risk_limit:
        return "exceeds"
    return "ambiguous"


def quantum_reserve_search(
    pool: RegionalPool,
    point,
    risk_limit: float,
    estimator: FullCircuitEstimator,
    deeper=(0, 1, 2, 4, 8, 16, 32),
) -> dict:
    """Binary search over attainable payout levels using full-circuit estimates.

    The largest level is never exceeded, so it is known to meet any limit.
    Each probe runs amplitude estimation; an ambiguous interval triggers one
    deeper schedule before the probe is reported as unresolved.
    """
    rates = pool.rates(point)
    levels = payout_levels(pool)
    low, high = -1, len(levels) - 1
    probes = []
    while high - low > 1:
        mid = (low + high) // 2
        result = estimator.estimate(pool, rates, int(levels[mid]))
        verdict = decide(result, risk_limit)
        if verdict == "ambiguous":
            result = estimator.estimate(pool, rates, int(levels[mid]), deeper)
            verdict = decide(result, risk_limit)
        probes.append({**result, "verdict": verdict})
        if verdict == "meets":
            high = mid
        elif verdict == "exceeds":
            low = mid
        else:
            break
    resolved = high - low == 1
    # Without a decision the minimum lies above the last level shown to fall
    # short and at or below the last level shown to be enough.
    lower = float(levels[low + 1]) / 400
    return {
        "point": [float(x) for x in point],
        "risk_limit": risk_limit,
        "reserve_usd": float(levels[high]) / 400 if resolved else None,
        "lower_usd": lower,
        "upper_usd": float(levels[high]) / 400,
        "resolved": resolved,
        "probes": probes,
        "queries": int(sum(p["queries"] for p in probes)),
    }
