"""Recompute the technical proposal's figures and run independent cross-checks.

Every number written here is computed by the engine in this run. Proposal
figures are validation targets: each check records the target, the computed
value and whether they agree. Discrepancies are kept, not hidden.

Usage: python -m liquidity_reserves.validation [--quick] [--output results/validation.json]
"""

from __future__ import annotations

import argparse
import json
import platform
import tracemalloc
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import scipy

from . import APPLICANT, APPLICANT_ROLE, __version__
from .bounds import bracket_reserve, grid_reserve
from .contraction import choose_registers, distribution, local_tensors
from .exact import ctmc_curve
from .examples import DEFAULT_RANGE, public_two_bond, synthetic_chain
from .money import format_usd, usd_to_units
from .paths import benchmark_dir, ensure_qjlb, results_dir
from .portfolio import SharedShockRange

M80 = usd_to_units(80_000_000)


class Checks:
    def __init__(self):
        self.rows = []

    def add(self, group, name, target, computed, passed, note="", display=None):
        self.rows.append(
            {
                "group": group,
                "check": name,
                "proposal_target": target,
                "computed": computed,
                "computed_display": display if display is not None else computed,
                "status": "pass" if passed else "discrepancy",
                "note": note,
            }
        )

    def money(self, group, name, target_usd, units, note=""):
        computed = units / 400
        self.add(
            group,
            name,
            format_usd(usd_to_units(target_usd)),
            computed,
            units == usd_to_units(target_usd),
            note,
            format_usd(units),
        )

    def percent(self, group, name, target_percent, value, decimals=3, note=""):
        shown = f"{100 * value:.{decimals}f}%"
        self.add(
            group,
            name,
            f"{target_percent:.{decimals}f}%",
            value,
            shown == f"{target_percent:.{decimals}f}%",
            note,
            shown,
        )

    def count(self, group, name, target, value, note=""):
        self.add(group, name, target, int(value), int(value) == target, note)


def _bracket_text(result):
    if result.resolved:
        return format_usd(result.upper_units)
    return f"{format_usd(result.lower_units)} to {format_usd(result.upper_units)}"


def two_bond_checks(checks: Checks) -> dict:
    from .envelope import two_country_envelope

    group = "Public two-bond example"
    pool = public_two_bond()
    loose = bracket_reserve(pool, DEFAULT_RANGE, 0.015)
    strict = bracket_reserve(pool, DEFAULT_RANGE, 0.005)
    grid_loose = grid_reserve(pool, DEFAULT_RANGE, 0.015, subdivisions=16)
    grid_strict = grid_reserve(pool, DEFAULT_RANGE, 0.005, subdivisions=16)
    envelope = two_country_envelope(pool, DEFAULT_RANGE, [0.015, 0.005], cells=200)

    checks.money(
        group, "Base reserve at the 1.5% three-year limit", 80_000_000, loose.base.lower_units
    )
    checks.money(
        group, "Reserve across the dependence range at 1.5%", 87_500_000, loose.upper_units
    )
    checks.add(group, "1.5% bracket resolved", True, loose.resolved, loose.resolved)
    checks.count(group, "Adaptive distribution evaluations at 1.5%", 7, loose.total_evaluations)
    checks.money(
        group, "Reserve across the dependence range at 0.5%", 105_000_000, strict.upper_units
    )
    checks.add(group, "0.5% bracket resolved", True, strict.resolved, strict.resolved)
    checks.count(group, "Adaptive distribution evaluations at 0.5%", 39, strict.total_evaluations)
    checks.count(
        group, "16x16 grid distribution evaluations at 0.5%", 517, grid_strict.total_evaluations
    )
    checks.count(
        group, "16x16 grid distribution evaluations at 1.5%", 517, grid_loose.total_evaluations
    )
    grid_gap = grid_strict.upper_units - grid_strict.lower_units
    checks.add(
        group,
        "16x16 grid result at 0.5%",
        "same $105,000,000 as adaptive refinement",
        grid_gap / 400,
        grid_gap == 0 and grid_strict.upper_units == strict.upper_units,
        "Corrected in the proposal on 16 November 2026: an earlier draft said the grid left a "
        "$2.5m bracket. That bracket "
        f"({format_usd(envelope['reserves'][1]['lower_units'])} to "
        f"{format_usd(envelope['reserves'][1]['upper_units'])}) comes from the 200x200 grid "
        "with the Poisson coupling bound.",
        f"{format_usd(grid_gap)} wide ({_bracket_text(grid_strict)})",
    )

    checks.percent(group, "P(payout > $80m), base", 1.205, loose.base.curve.exceedance(M80))
    observed, point = loose.tracker.exceedance_at(M80)
    checks.percent(
        group,
        "P(payout > $80m), highest feasible scenario",
        1.607,
        observed,
        note=f"at earthquake multiplier {point[0]:g}, rain share {point[1]:g}",
    )
    index = int(np.flatnonzero(envelope["knots_units"] == M80)[0])
    checks.percent(
        group,
        "P(payout > $80m), bounded (200x200 grid plus coupling bound)",
        1.631,
        float(envelope["bounded_exceedance"][index]),
        note=f"coupling allowance {envelope['coupling_bound']:.3e}; the adaptive cell bounds "
        f"after the 0.5% refinement give {100 * strict.bound_at(M80):.3f}%",
    )
    base_excess = loose.base.curve.expected_excess(M80)
    checks.add(
        group,
        "Expected payout above $80m, base",
        "$282,355",
        base_excess,
        f"${base_excess:,.0f}" == "$282,355",
        display=f"${base_excess:,.2f}",
    )
    worst_excess = loose.tracker.excess_at(M80)
    checks.add(
        group,
        "Expected payout above $80m, highest feasible scenario",
        "$385,043",
        worst_excess,
        f"${worst_excess:,.0f}" == "$385,043",
        display=f"${worst_excess:,.2f}",
    )
    for row, limit in zip(envelope["reserves"], (0.015, 0.005), strict=True):
        checks.add(
            "Independent checks",
            f"Coupling-grid envelope reserve at {100 * limit:g}% (Markov generator laws)",
            "brackets the adaptive result",
            [row["lower_units"] / 400, row["upper_units"] / 400],
            row["lower_units"]
            <= (loose if limit == 0.015 else strict).upper_units
            <= row["upper_units"],
            display=f"{format_usd(row['lower_units'])} to {format_usd(row['upper_units'])}",
        )
    for limit, adaptive in ((0.015, loose), (0.005, strict)):
        independent = bracket_reserve(pool, DEFAULT_RANGE, limit, method="ctmc")
        same = (
            independent.lower_units == adaptive.lower_units
            and independent.upper_units == adaptive.upper_units
            and independent.total_evaluations == adaptive.total_evaluations
        )
        checks.add(
            "Independent checks",
            f"Reserve bounds at {100 * limit:g}% recomputed with the state-space solver",
            _bracket_text(adaptive),
            [independent.lower_units / 400, independent.upper_units / 400],
            same,
            f"{independent.total_evaluations} evaluations",
            _bracket_text(independent),
        )
    return {
        "loose": loose,
        "strict": strict,
        "grid_loose": grid_loose,
        "grid_strict": grid_strict,
        "envelope": envelope,
    }


def synthetic_checks(checks: Checks, quick: bool) -> list:
    rows = [(5, 136_250_000, 136_250_000, 567, 0)]
    if not quick:
        rows += [
            (10, 191_250_000, 191_500_000, 487, 250_000),
            (20, 289_000_000, 290_000_000, 379, 1_000_000),
        ]
    table = []
    for countries, low, high, evaluations, gap in rows:
        group = f"Synthetic chain, {countries} countries"
        pool = synthetic_chain(countries)
        result = bracket_reserve(pool, DEFAULT_RANGE, 0.015, gap_usd=gap)
        grid = grid_reserve(pool, DEFAULT_RANGE, 0.015, subdivisions=16)
        target = format_usd(usd_to_units(low)) + (
            "" if low == high else f" to {format_usd(usd_to_units(high))}"
        )
        checks.add(
            group,
            "Reserve across the dependence range at 1.5%",
            target,
            [result.lower_units / 400, result.upper_units / 400],
            result.lower_units == usd_to_units(low) and result.upper_units == usd_to_units(high),
            f"gap tolerance {format_usd(usd_to_units(gap))}; status {result.status}",
            _bracket_text(result),
        )
        checks.count(
            group, "Adaptive distribution evaluations", evaluations, result.total_evaluations
        )
        checks.count(group, "16x16 grid distribution evaluations", 517, grid.total_evaluations)
        table.append(
            {
                "countries": countries,
                "base_reserve": format_usd(result.base.lower_units),
                "adaptive": _bracket_text(result),
                "adaptive_evaluations": result.total_evaluations,
                "grid": _bracket_text(grid),
                "grid_evaluations": grid.total_evaluations,
                "payout_levels": result.registers.upper.size,
            }
        )
    return table


def organizer_reference_checks(checks: Checks):
    ensure_qjlb()
    from qjlb.bond import BondModel

    worst = 0.0
    names = []
    for path in sorted((benchmark_dir() / "scenarios").glob("adb-*.json")):
        model = BondModel.load(path)
        pool = public_two_bond(path.stem)
        registers = choose_registers(pool)
        curve = distribution(pool, pool.rates(pool.base_point), registers.lower)
        term = model.term_distribution()
        loss = model.principal_loss(model.states).sum(axis=1)
        reference = np.array([term[loss > x / 400].sum() for x in curve.support_units])
        worst = max(worst, float(np.max(np.abs(reference - curve.tail))))
        names.append(path.stem)
    checks.add(
        "Independent checks",
        "Payout exceedance curves vs organizer's exact reference (5 ADB scenarios)",
        "agree to numerical precision",
        worst,
        worst < 1e-12,
        ", ".join(names),
        f"max abs difference {worst:.2e}",
    )


def state_space_checks(checks: Checks):
    rng = np.random.default_rng(20261113)
    worst_curve, worst_violation, count = 0.0, -np.inf, 0
    for n in (2, 3, 5):
        pool = public_two_bond() if n == 2 else synthetic_chain(n)
        registers = choose_registers(pool)
        support = registers.upper.support_units
        for _ in range(12):
            corners = np.sort(rng.random((2, 2)), axis=0) * [2.0, 0.25]
            low, high = corners
            point = rng.uniform(low, high)
            rates = pool.rates(point)
            exact = ctmc_curve(pool, rates, support)
            tensor = distribution(pool, rates, registers.lower)
            bound = distribution(pool, pool.dominating_rates(low, high), registers.upper)
            worst_curve = max(worst_curve, float(np.max(np.abs(exact.tail - tensor.tail))))
            worst_violation = max(worst_violation, float(np.max(exact.tail - bound.tail)))
            count += 1
    checks.add(
        "Independent checks",
        f"Tensor contraction vs state-space solver at {count} interior points (2, 3, 5 countries)",
        "36 points agree to numerical precision",
        worst_curve,
        count == 36 and worst_curve < 1e-12,
        display=f"max abs difference {worst_curve:.2e}",
    )
    checks.add(
        "Independent checks",
        f"Dominating cell bound never below the state-space exceedance ({count} points)",
        "no violation",
        worst_violation,
        worst_violation <= 1e-15,
        display=f"largest (exact - bound) {worst_violation:.2e}",
    )


def marginal_checks(checks: Checks):
    pool = synthetic_chain(5)
    worst = 0.0
    laws = []
    for corner in SharedShockRange((0.0, 2.0), (0.0, 0.25)).corners():
        tensors, weights = local_tensors(pool, pool.rates(corner))
        marginals = []
        for i in range(pool.n):
            vector_left = np.ones(1)
            for j in range(i):
                vector_left = (vector_left @ tensors[j].sum(axis=2)) * weights[j]
            vector_right = np.ones(1)
            for j in range(pool.n - 1, i, -1):
                vector_right = tensors[j].sum(axis=2) @ vector_right
                vector_right = weights[j - 1] * vector_right
            marginals.append(np.einsum("a,abo,b->o", vector_left, tensors[i], vector_right))
        laws.append(np.array(marginals))
    for law in laws[1:]:
        worst = max(worst, float(np.max(np.abs(law - laws[0]))))
    checks.add(
        "Independent checks",
        "Each country's marginal payout law is unchanged across the range corners (5 countries)",
        "unchanged",
        worst,
        worst < 1e-14,
        display=f"max abs difference {worst:.2e}",
    )


def memory_rows(quick: bool) -> list:
    proposal = {2: 0.06, 5: 0.40, 10: 0.79, 20: 1.58, 50: 3.95}
    rows = []
    for n in (2, 5, 10, 20) if quick else (2, 5, 10, 20, 50):
        pool = synthetic_chain(n)
        registers = choose_registers(pool)
        rates = pool.rates(pool.base_point)
        tracemalloc.start()
        distribution(pool, rates, registers.lower)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        rows.append(
            {
                "countries": n,
                "payout_levels": registers.lower.size,
                "peak_traced_mib": round(peak / 2**20, 2),
                "proposal_mib": proposal[n],
            }
        )
    return rows


def adb_target_checks(checks: Checks):
    ensure_qjlb()
    from .benchmark import adb_term_targets
    from .examples import load_scenario, load_terms

    terms = load_terms()
    base, _, _ = adb_term_targets(load_scenario("adb-base"), terms)
    independent, _, _ = adb_term_targets(load_scenario("adb-independent"), terms)
    group = "Public two-bond example"
    loss = base["term_expected_principal_loss_usd"]
    checks.add(
        group,
        "Expected principal loss, base",
        "near $10.06 million",
        loss,
        f"{loss / 1e6:.2f}" == "10.06",
        display=f"${loss:,.0f}",
    )
    checks.add(
        group,
        "Expected principal loss, common shocks removed",
        "near $10.06 million",
        independent["term_expected_principal_loss_usd"],
        f"{independent['term_expected_principal_loss_usd'] / 1e6:.2f}" == "10.06",
        display=f"${independent['term_expected_principal_loss_usd']:,.0f}",
    )
    checks.percent(
        group,
        "Joint attachment, base",
        4.41,
        base["term_joint_attachment_probability"],
        decimals=2,
    )
    checks.percent(
        group,
        "Joint attachment, common shocks removed",
        2.72,
        independent["term_joint_attachment_probability"],
        decimals=2,
    )


def benchmark_checks(checks: Checks, summary_path: Path):
    if not summary_path.is_file():
        checks.add(
            "Benchmark",
            "Official evaluator results",
            "65 metric checks pass",
            None,
            False,
            f"No evaluator summary at {summary_path}; run scripts/run_benchmark.sh on Linux.",
        )
        return None
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    totals = summary["totals"]
    checks.add(
        "Benchmark",
        "Metric checks passed (public scenarios and seeded practice cases)",
        "65 of 65",
        [totals["metric_passes"], totals["metric_checks"]],
        totals["metric_passes"] == totals["metric_checks"] == 65,
        f"{totals['cases_passing']}/{totals['cases']} cases; "
        f"{totals['valid_runs']}/{totals['runs']} team runs valid",
        f"{totals['metric_passes']} of {totals['metric_checks']}",
    )
    worst = totals["worst_normalised_rmse"]
    checks.add(
        "Benchmark",
        "Largest error as a share of its allowed tolerance",
        "5.1e-13",
        worst,
        worst is not None and f"{worst:.1e}" == "5.1e-13",
        "Corrected in the proposal on 16 November 2026 to this evaluator run; an earlier draft "
        "quoted 2.1e-13 from a previous prototype. The value is floating-point rounding and "
        "changes with platform and BLAS; every case stays below 1e-12 of its tolerance.",
        display=f"{worst:.2e}" if worst is not None else "n/a",
    )
    return summary


def quantum_checks(checks: Checks, path: Path):
    """Claims about the tensor-chain quantum circuit, from the recorded quantum study."""
    group = "Quantum circuit"
    if not path.is_file():
        checks.add(
            group,
            "Recorded quantum study",
            "present",
            None,
            False,
            f"No quantum study at {path}; run python -m liquidity_reserves.quantum.",
        )
        return None
    study = json.loads(path.read_text(encoding="utf-8"))
    if "verification" not in study:
        checks.add(
            group,
            "Recorded quantum study",
            "tensor-chain circuit",
            None,
            False,
            "The recorded study predates the tensor-chain circuit; rerun it.",
        )
        return None
    two = study["verification"]["two_countries"]
    three = study["verification"]["three_countries"]
    checks.add(group, "Two-bond circuit size", "21 qubits", two["qubits"], two["qubits"] == 21)
    for label, row in (("2 countries", two), ("3 countries", three)):
        checks.add(
            group,
            f"Loaded outcome law vs exact joint law, {label}",
            "agrees to numerical precision",
            row["loading_max_error"],
            row["loading_max_error"] < 1e-12,
            display=f"max error {row['loading_max_error']:.2e}",
        )
        arithmetic = row["arithmetic"]
        checks.add(
            group,
            f"Payout adder and reserve flag, every outcome combination, {label}",
            "all exact",
            arithmetic["combinations"],
            arithmetic["register_correct"] and arithmetic["flag_correct"],
            display=f"{arithmetic['combinations']} combinations exact",
        )
    checks.add(
        group,
        "Full two-bond circuit vs amplitude rotation, Grover powers "
        + ", ".join(str(k) for k in two["full_circuit_powers"]),
        "agrees to numerical precision",
        two["full_circuit_max_error"],
        two["full_circuit_max_error"] < 1e-9,
        display=f"max deviation {two['full_circuit_max_error']:.2e}",
    )
    for search in study["reserve_search"]:
        limit = search["risk_limit"]
        classical = search["classical_reserve_usd"]
        if search["resolved"]:
            passed = search["reserve_usd"] == classical
            shown = format_usd(usd_to_units(search["reserve_usd"]))
        else:
            passed = search["lower_usd"] <= classical <= search["upper_usd"]
            shown = (
                f"{format_usd(usd_to_units(search['lower_usd']))} to "
                f"{format_usd(usd_to_units(search['upper_usd']))} (bracket)"
            )
        checks.add(
            group,
            f"Quantum reserve search, base assumptions, {100 * limit:g}% limit",
            format_usd(usd_to_units(classical)),
            search["reserve_usd"]
            if search["resolved"]
            else [search["lower_usd"], search["upper_usd"]],
            passed,
            f"{len(search['probes'])} probes, {search['queries']:,} queries"
            + ("" if search["resolved"] else "; a probe near the limit stayed undecided"),
            shown,
        )
    for row in study.get("interval_coverage", []):
        checks.add(
            group,
            f"Interval coverage, Grover powers up to {row['max_power']}, {row['repeats']} repeats",
            "at least 95%",
            row["coverage"],
            row["coverage"] >= row["nominal"],
            display=f"{100 * row['coverage']:.1f}%",
        )
    cert = study["bound_certification"]
    checks.add(
        group,
        f"Both {format_usd(usd_to_units(cert['upper_usd']))} bounds at 1.5% confirmed by "
        "quantum estimates",
        "confirmed",
        cert["certified"],
        cert["certified"],
        f"smaller reserve {cert['witness']['verdict']}; dominating cells "
        + ", ".join(c["verdict"] for c in cert["dominating_cells"]),
    )
    synthetic = {r["countries"]: r for r in study["resources"] if r["case"].startswith("Synth")}
    if 2 in synthetic and 50 in synthetic:
        per_country = (synthetic[50]["qubits"] - synthetic[2]["qubits"]) / 48
        checks.add(
            group,
            "Qubits added per country (synthetic chains, 2 to 50 countries)",
            "about 8",
            per_country,
            7.5 <= per_country <= 8.5,
            display=f"{per_country:.2f}",
        )
    return study


def extension_checks(checks: Checks, folder: Path):
    """Range evidence, financing, pooling and the map surrogate, recomputed here.

    QAOA and the noise study need the quantum extras and take minutes, so their
    recorded results are checked against the figures quoted in the documents.
    """
    from .contraction import choose_registers
    from .evidence import evidence_reserves, public_evidence
    from .examples import DEFAULT_RANGE
    from .insights import assumption_map
    from .layering import risk_layering
    from .pooling import pooling_benefit
    from .surrogate import surrogate_map

    pool = public_two_bond()
    group = "Range evidence (World Bank catalogue)"
    evidence = public_evidence(pool)
    checks.count(
        group,
        "Earthquake events triggering both countries",
        0,
        evidence["earthquake"]["joint_trigger_events"],
    )
    checks.count(
        group, "Rain events triggering both countries", 1, evidence["rain"]["joint_trigger_events"]
    )
    reserves = {(r["range"], r["risk_limit"]): r for r in evidence_reserves(evidence, (0.015,))}
    evidence_row = reserves[("evidence_range", 0.015)]
    checks.money(
        group,
        "Certified reserve across the evidence range at 1.5%",
        80_000_000,
        int(round(evidence_row["upper_usd"] * 400)),
        "range 0-1x earthquake, 0-25% rain",
    )

    group = "Financing and pooling"
    registers = choose_registers(pool)
    result = bracket_reserve(pool, DEFAULT_RANGE, 0.015, registers=registers)
    plan = risk_layering(pool, DEFAULT_RANGE, 0.015, result=result)
    layers = plan["optimal"]["layers_usd"]
    checks.add(
        group,
        "Optimal stack at 1.5%",
        "credit to $70m, insurance $70m-$87.5m",
        layers,
        layers["reserve"] == [0.0, 0.0]
        and layers["credit"] == [0.0, 70e6]
        and layers["insurance"] == [70e6, 87.5e6],
        display=f"credit to ${layers['credit'][1] / 1e6:g}m, insurance "
        f"${layers['insurance'][0] / 1e6:g}m-${layers['insurance'][1] / 1e6:g}m",
    )
    saving = plan["saving_vs_all_reserve_percent"]
    checks.add(
        group,
        "Expected funding cost below an all-cash reserve",
        "16.1%",
        saving,
        f"{saving:.1f}%" == "16.1%",
        display=f"{saving:.1f}%",
    )
    pooled = pooling_benefit(pool, DEFAULT_RANGE, 0.015, result=result)
    checks.add(
        group,
        "Pooling saving at 1.5% ($140m standalone vs $87.5m pooled)",
        "37.5%",
        pooled["saving_percent"],
        f"{pooled['saving_percent']:.1f}%" == "37.5%",
        display=f"{pooled['saving_percent']:.1f}%",
    )

    group = "Map surrogate (Gaussian process)"
    for limit in (0.015, 0.005):
        bracket = (
            result
            if limit == 0.015
            else bracket_reserve(pool, DEFAULT_RANGE, limit, registers=registers)
        )
        exact = assumption_map(pool, DEFAULT_RANGE, limit, registers.lower, bracket.upper_units, 21)
        fast = surrogate_map(pool, DEFAULT_RANGE, limit, registers.lower, bracket.upper_units, 21)
        same = exact["reserve_usd"] == fast["reserve_usd"]
        checks.add(
            group,
            f"Surrogate map equals the exact 21x21 map at {limit:.1%}",
            "all 441 cells, at most 30 contractions",
            fast["evaluations"],
            same and fast["evaluations"] <= 30,
            display=f"{'all' if same else 'not all'} cells, {fast['evaluations']} contractions",
        )

    layering = folder / "layering.json"
    if layering.is_file():
        record = json.loads(layering.read_text(encoding="utf-8"))
        runs = {r["mixer"]: r for r in record.get("qaoa") or []}
        xy = runs.get("XY ring (one-hot preserving)")
        if xy:
            checks.add(
                "Quantum optimization (recorded)",
                "XY-mixer QAOA samples the optimal stack",
                "most frequent valid plan is optimal, P > 20%",
                xy["probability_optimal_sampled"],
                xy["qaoa_finds_optimum"] and xy["probability_optimal_sampled"] > 0.2,
                f"{xy['qubits']} qubits, {xy['compiled_cx']} CX",
                f"{100 * xy['probability_optimal_sampled']:.1f}%",
            )
    noise = folder / "noise.json"
    if noise.is_file():
        record = json.loads(noise.read_text(encoding="utf-8"))
        budget = next(
            r
            for r in record["error_budget"]
            if r["case"] == "Public two-bond" and r["grover_depth"] == 16
        )
        checks.add(
            "Noise study (recorded)",
            "Two-qubit error needed, two-bond circuit, depth 16",
            "about 1e-5",
            budget["error_needed_for_half_fidelity"],
            5e-6 < budget["error_needed_for_half_fidelity"] < 2e-5,
            display=f"{budget['error_needed_for_half_fidelity']:.1e}",
        )


def run(quick: bool = False, benchmark_summary: Path | None = None) -> dict:
    checks = Checks()
    two_bond = two_bond_checks(checks)
    adb_target_checks(checks)
    synthetic = synthetic_checks(checks, quick)
    organizer_reference_checks(checks)
    state_space_checks(checks)
    marginal_checks(checks)
    memory = memory_rows(quick)
    for row in memory:
        # tracemalloc counts Python-level allocations, which vary slightly with
        # the numpy build; agreement within 5% (or 0.01 MiB) is treated as a match.
        tolerance = max(0.01, 0.05 * row["proposal_mib"])
        checks.add(
            "Resource estimate",
            f"Peak traced memory of one contraction, {row['countries']} synthetic countries",
            f"{row['proposal_mib']:.2f} MiB",
            row["peak_traced_mib"],
            abs(row["peak_traced_mib"] - row["proposal_mib"]) <= tolerance,
            f"{row['payout_levels']:,} payout levels; tracemalloc peak during factor "
            "construction and contraction",
            f"{row['peak_traced_mib']:.2f} MiB",
        )
    benchmark = benchmark_checks(
        checks, benchmark_summary or results_dir() / "benchmark" / "summary.json"
    )
    quantum_checks(checks, results_dir() / "quantum.json")
    extension_checks(checks, results_dir())
    loose, strict = two_bond["loose"], two_bond["strict"]
    return {
        "schema": "dlr-validation-v1",
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "applicant": f"{APPLICANT} ({APPLICANT_ROLE})",
        "engine_version": __version__,
        "quick": quick,
        "environment": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "scipy": scipy.__version__,
            "platform": platform.platform(),
        },
        "counting_rule": (
            "One distribution evaluation is one aggregate payout distribution. Adaptive "
            "refinement counts the base point, each distinct range corner, and a centre "
            "scenario plus a dominating model for every cell created. The k-by-k grid counts "
            "the base point, four corners and 2k^2 cell evaluations (517 for k = 16)."
        ),
        "summary": {
            "checks": len(checks.rows),
            "passed": sum(r["status"] == "pass" for r in checks.rows),
            "discrepancies": [r["check"] for r in checks.rows if r["status"] != "pass"],
        },
        "checks": checks.rows,
        "reserve_table": [
            {
                "portfolio": "Public two-bond / 1.5%",
                "reserve": _bracket_text(loose),
                "adaptive_evaluations": loose.total_evaluations,
                "grid": _bracket_text(two_bond["grid_loose"]),
                "grid_evaluations": two_bond["grid_loose"].total_evaluations,
            },
            {
                "portfolio": "Public two-bond / 0.5%",
                "reserve": _bracket_text(strict),
                "adaptive_evaluations": strict.total_evaluations,
                "grid": _bracket_text(two_bond["grid_strict"]),
                "grid_evaluations": two_bond["grid_strict"].total_evaluations,
            },
        ]
        + [
            {
                "portfolio": f"Synthetic {row['countries']} countries / 1.5%",
                "reserve": row["adaptive"],
                "adaptive_evaluations": row["adaptive_evaluations"],
                "grid": row["grid"],
                "grid_evaluations": row["grid_evaluations"],
            }
            for row in synthetic
        ],
        "memory": memory,
        "benchmark": None
        if benchmark is None
        else {"totals": benchmark["totals"], "source": "results/benchmark/summary.json"},
    }


def to_markdown(report: dict) -> str:
    lines = [
        "# Validation report",
        "",
        f"Generated {report['generated_utc']} by engine {report['engine_version']}"
        f" ({'quick' if report['quick'] else 'full'} run). {report['applicant']}.",
        "",
        f"{report['summary']['passed']} of {report['summary']['checks']} checks agree with "
        "their targets.",
        "",
        "| Group | Check | Proposal / target | Computed | Status | Note |",
        "|---|---|---|---|---|---|",
    ]
    for row in report["checks"]:
        lines.append(
            f"| {row['group']} | {row['check']} | {row['proposal_target']} | "
            f"{row['computed_display']} | {row['status']} | {row['note']} |"
        )
    lines += [
        "",
        "## Reserve brackets and evaluation counts",
        "",
        "| Portfolio / limit | Adaptive | Evaluations | 16x16 grid | Evaluations |",
        "|---|---|---|---|---|",
    ]
    for row in report["reserve_table"]:
        lines.append(
            f"| {row['portfolio']} | {row['reserve']} | {row['adaptive_evaluations']} | "
            f"{row['grid']} | {row['grid_evaluations']} |"
        )
    lines += [
        "",
        "## Peak traced memory of one contraction",
        "",
        "| Countries | Payout levels | Measured MiB | Proposal MiB |",
        "|---|---|---|---|",
    ]
    for row in report["memory"]:
        lines.append(
            f"| {row['countries']} | {row['payout_levels']:,} | {row['peak_traced_mib']} | "
            f"{row['proposal_mib']} |"
        )
    lines += ["", f"Counting rule: {report['counting_rule']}", ""]
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--quick", action="store_true", help="skip the 10- and 20-country rows")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--benchmark-summary", type=Path, default=None)
    args = parser.parse_args(argv)
    report = run(args.quick, args.benchmark_summary)
    output = args.output or results_dir() / "validation.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    output.with_suffix(".md").write_text(to_markdown(report), encoding="utf-8")
    summary = report["summary"]
    print(f"{summary['passed']}/{summary['checks']} checks agree. Report: {output}")
    for name in summary["discrepancies"]:
        print(f"  discrepancy: {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
