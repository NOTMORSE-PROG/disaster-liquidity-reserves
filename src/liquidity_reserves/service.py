"""Request handling for the local app: parse analyst inputs, run the engine, build reports.

Everything shown in the browser or exported comes from these functions, so
the HTTP layer stays thin and the logic is testable without a server.
"""

from __future__ import annotations

import copy
import json
import math
from datetime import datetime, timezone
from functools import lru_cache

import numpy as np

from . import APPLICANT, APPLICANT_ROLE, PROJECT_TITLE, __version__
from .bounds import (
    DEFAULT_MAX_EVALUATIONS,
    GUARD,
    MAX_EVALUATIONS_LIMIT,
    BracketResult,
    bracket_reserve,
    grid_reserve,
)
from .contraction import choose_registers, distribution
from .examples import (
    DEFAULT_RANGE,
    DEFAULT_RISK_LIMIT,
    example_catalogue,
    public_two_bond,
    synthetic_chain,
)
from .insights import (
    assumption_map,
    chain_graph,
    limit_sweep,
    map_resolution,
    shortfall_drivers,
)
from .money import decimal_string, format_usd, usd_to_units
from .paths import results_dir
from .portfolio import Country, InputError, RegionalPool, SharedShockRange

MAX_CHART_POINTS = 800
DEFAULT_SWEEP_PERCENT = (0.25, 0.5, 1.0, 1.5, 2.0, 3.0, 5.0)
_CACHE: dict = {}
_CACHE_SIZE = 8
STATUS_TEXT = {
    "resolved": "Resolved: the lower and upper reserve bounds agree.",
    "within_gap": "Bracket within the requested gap tolerance.",
    "budget": "Unresolved: the evaluation budget ran out before the bounds met.",
    "resolution": "Unresolved: the remaining cells are too small to split further; the "
    "limit sits within floating-point precision of a payout level.",
    "grid": "Unresolved on this grid.",
}


# ---------------------------------------------------------------------------
# Inputs


@lru_cache(maxsize=64)
def _example_pool(example: str, countries: int) -> RegionalPool:
    if example == "public-two-bond":
        return public_two_bond()
    if example == "synthetic-chain":
        return synthetic_chain(countries)
    raise InputError(f"unknown example '{example}'")


def _number(payload, key, label, errors, minimum=None, maximum=None, integer=False):
    value = payload.get(key)
    if isinstance(value, str):
        value = value.strip()
        try:
            value = float(value) if value else None
        except ValueError:
            errors.append(f"{label} must be a number")
            return None
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        errors.append(f"{label} is required and must be a number")
        return None
    if not math.isfinite(value):
        errors.append(f"{label} must be finite")
        return None
    if integer:
        if value != int(value):
            errors.append(f"{label} must be a whole number")
            return None
        value = int(value)
    if minimum is not None and value < minimum:
        errors.append(f"{label} must be at least {minimum:g}")
        return None
    if maximum is not None and value > maximum:
        errors.append(f"{label} must be at most {maximum:g}")
        return None
    return value


def default_inputs(example: str = "public-two-bond", countries: int = 5) -> dict:
    pool = _example_pool(example, countries)
    return {
        "example": example,
        "synthetic_countries": countries,
        "risk_limit_percent": DEFAULT_RISK_LIMIT * 100,
        "earthquake_multiplier_low": DEFAULT_RANGE.earthquake_multiplier[0],
        "earthquake_multiplier_high": DEFAULT_RANGE.earthquake_multiplier[1],
        "rain_share_percent_low": DEFAULT_RANGE.rain_share[0] * 100,
        "rain_share_percent_high": DEFAULT_RANGE.rain_share[1] * 100,
        "base_earthquake_multiplier": pool.base_point[0],
        "base_rain_share_percent": pool.base_point[1] * 100,
        "term_years": pool.term_years,
        "gap_usd": 0,
        "max_evaluations": DEFAULT_MAX_EVALUATIONS,
        # The grid comparison costs 517 extra evaluations; keep it quick by default.
        "compare_grid": countries <= 10 or example == "public-two-bond",
        "grid_subdivisions": 16,
        "countries": [
            {
                "code": c.code,
                "name": c.name,
                "earthquake_rate": c.earthquake_rate,
                "rain_rate": c.rain_rate,
                "earthquake_cap_usd": decimal_string(c.earthquake_cap_units),
                "rain_payment_usd": decimal_string(c.rain_cap_units),
                "severity": list(c.severity),
            }
            for c in pool.countries
        ],
        "base_shared_earthquake": list(pool.base_shared_earthquake),
        "pairs": pool.pair_labels,
        "synthetic": pool.synthetic,
        "description": pool.description,
        "name": pool.name,
    }


def parse_inputs(payload: dict):
    """Validate a calculation request. Raises InputError listing every problem."""
    if not isinstance(payload, dict):
        raise InputError("request body must be a JSON object")
    errors = []
    example = payload.get("example", "public-two-bond")
    if example not in {e["id"] for e in example_catalogue()}:
        raise InputError(f"unknown example '{example}'")
    countries = 2
    if example == "synthetic-chain":
        countries = _number(
            payload, "synthetic_countries", "Number of synthetic countries", errors, 2, 20, True
        )
        if countries is None:
            raise InputError(errors)
    template = _example_pool(example, countries)
    limit = _number(payload, "risk_limit_percent", "Risk limit (%)", errors, 0, 100)
    if limit is not None and not 0 < limit < 100:
        errors.append("Risk limit (%) must be strictly between 0 and 100")
        limit = None
    quake_low = _number(payload, "earthquake_multiplier_low", "Shared earthquake low", errors, 0)
    quake_high = _number(payload, "earthquake_multiplier_high", "Shared earthquake high", errors, 0)
    rain_low = _number(payload, "rain_share_percent_low", "Shared rain low (%)", errors, 0, 100)
    rain_high = _number(payload, "rain_share_percent_high", "Shared rain high (%)", errors, 0, 100)
    base_quake = _number(
        payload, "base_earthquake_multiplier", "Base shared earthquake multiplier", errors, 0
    )
    base_rain = _number(
        payload, "base_rain_share_percent", "Base shared rain share (%)", errors, 0, 100
    )
    term = _number(payload, "term_years", "Term (years)", errors, 1, 30, True)
    gap = _number(payload, "gap_usd", "Gap tolerance (USD)", errors, 0, 1e12)
    budget = _number(
        payload,
        "max_evaluations",
        "Evaluation budget",
        errors,
        7,
        MAX_EVALUATIONS_LIMIT,
        True,
    )
    grid = None
    if payload.get("compare_grid"):
        grid = _number(payload, "grid_subdivisions", "Grid cells per axis", errors, 1, 32, True)
    if None not in (quake_low, quake_high) and quake_low > quake_high:
        errors.append("Shared earthquake low exceeds high")
    if None not in (rain_low, rain_high) and rain_low > rain_high:
        errors.append("Shared rain low exceeds high")
    members = list(template.countries)
    rows = payload.get("countries")
    if rows is not None:
        if not isinstance(rows, list) or len(rows) != len(members):
            errors.append(f"Country table must have {len(members)} rows")
        else:
            members = []
            for country, row in zip(template.countries, rows, strict=True):
                label = country.code
                row = row if isinstance(row, dict) else {}
                quake = _number(row, "earthquake_rate", f"{label} earthquake rate", errors, 0, 10)
                rain = _number(row, "rain_rate", f"{label} rain rate", errors, 0, 10)
                caps = []
                for key, name in (
                    ("earthquake_cap_usd", "earthquake cap"),
                    ("rain_payment_usd", "rain payment"),
                ):
                    try:
                        caps.append(usd_to_units(row.get(key), f"{label} {name}"))
                    except ValueError as error:
                        errors.append(str(error))
                        caps.append(None)
                if None in (quake, rain) or None in caps:
                    members.append(country)
                    continue
                try:
                    members.append(
                        Country(
                            code=country.code,
                            name=country.name,
                            earthquake_rate=quake,
                            severity=country.severity,
                            rain_rate=rain,
                            earthquake_cap_units=caps[0],
                            rain_cap_units=caps[1],
                        )
                    )
                except InputError as error:
                    errors += error.messages
    # Check feasibility whenever the values it needs are valid, so an analyst
    # sees range problems together with any other field errors.
    pool = shock_range = None
    if None not in (term, base_quake, base_rain):
        pool = RegionalPool(
            name=template.name,
            countries=tuple(members),
            term_years=term,
            base_shared_earthquake=template.base_shared_earthquake,
            base_point=(base_quake, base_rain / 100),
            synthetic=template.synthetic,
            description=template.description,
            source=template.source,
        )
        errors += [f"Base assumptions: {p}" for p in pool.infeasibilities(pool.base_point)]
    ranges = (quake_low, quake_high, rain_low, rain_high)
    if None not in ranges and quake_low <= quake_high and rain_low <= rain_high:
        shock_range = SharedShockRange((quake_low, quake_high), (rain_low / 100, rain_high / 100))
        if pool is not None:
            errors = [f"Range: {p}" for p in pool.infeasibilities(shock_range.high)] + errors
    if errors:
        raise InputError(errors)
    modified = members != list(template.countries) or term != template.term_years
    return {
        "pool": pool,
        "shock_range": shock_range,
        "risk_limit": limit / 100,
        "gap_usd": gap,
        "max_evaluations": budget,
        "grid": grid,
        "example": example,
        "synthetic_countries": countries,
        "modified": modified,
    }


# ---------------------------------------------------------------------------
# Results


def _usd(units) -> dict:
    return {"usd": decimal_string(units), "text": format_usd(units), "value": units / 400}


def _cells(count: int) -> str:
    return "1 cell" if count == 1 else f"{count} cells"


def _pct(value: float) -> str:
    return f"{100 * value:.3f}%"


def _point(pool: RegionalPool, point) -> dict:
    described = pool.describe_point(point)
    described["label"] = (
        f"shared earthquake rate {point[0]:g} x base, shared rain {100 * point[1]:.4g}% of the "
        "smaller neighbouring rain rate"
    )
    return described


def _rates(pool: RegionalPool, rates) -> list:
    rows = []
    for i, country in enumerate(pool.countries):
        rows.append(
            {
                "country": country.code,
                "private_earthquake_per_year": float(rates.earthquake_private[i]),
                "private_rain_per_year": float(rates.rain_private[i]),
            }
        )
    return rows


def _series(curve_support, tail, x_max) -> dict:
    """Step-curve points up to x_max, dropping repeats so lattice gaps add no points."""
    amounts = curve_support / 400
    keep = amounts <= x_max
    x, y = amounts[keep], tail[keep]
    if len(x) > 1:
        change = np.r_[True, y[1:] != y[:-1]]
        change[-1] = True
        x, y = x[change], y[change]
    if len(x) > MAX_CHART_POINTS:
        idx = np.unique(np.linspace(0, len(x) - 1, MAX_CHART_POINTS).astype(int))
        x, y = x[idx], y[idx]
    return {"x": x.tolist(), "y": y.tolist()}


def _chart(result: BracketResult) -> dict:
    limit = result.risk_limit
    envelope = result.upper_envelope()
    upper_support = result.registers.upper.support_units
    # Show the curves until the certified bound has fallen well below the limit.
    beyond = np.flatnonzero(envelope <= limit / 20)
    end_units = upper_support[beyond[0]] if len(beyond) else upper_support[-1]
    end_units = max(end_units, result.upper_units)
    x_max = float(end_units) / 400 * 1.04
    lower_support = result.registers.lower.support_units
    return {
        "x_max_usd": x_max,
        "risk_limit": limit,
        "series": [
            {
                "id": "base",
                "label": "Base assumptions",
                **_series(lower_support, result.base.curve.tail, x_max),
            },
            {
                "id": "observed",
                "label": "Highest feasible scenario",
                **_series(lower_support, result.tracker.worst_tail, x_max),
            },
            {
                "id": "bound",
                "label": "Certified upper bound",
                **_series(upper_support, envelope, x_max),
            },
        ],
        "markers": [
            {"id": "base", "label": "Base reserve", "usd": result.base.lower_units / 400},
            {"id": "lower", "label": "Lower bound", "usd": result.lower_units / 400},
            {"id": "upper", "label": "Upper bound", "usd": result.upper_units / 400},
        ],
    }


def _key_amounts(result: BracketResult) -> list:
    amounts = sorted({result.base.lower_units, result.lower_units, result.upper_units})
    evidence = result.shortfall_evidence()
    if evidence:
        amounts = sorted(set(amounts) | {evidence[0]})
    rows = []
    for units in amounts:
        observed, point = result.tracker.exceedance_at(units)
        rows.append(
            {
                "amount": _usd(units),
                "base_exceedance": result.base.curve.exceedance(units),
                "observed_exceedance": observed,
                "observed_at": {"earthquake_multiplier": point[0], "rain_share": point[1]},
                "bound_exceedance": result.bound_at(units),
                "base_expected_excess_usd": result.base.curve.expected_excess(units),
                "observed_expected_excess_usd": result.tracker.excess_at(units),
                "meets_limit_everywhere": result.bound_at(units) + GUARD <= result.risk_limit,
            }
        )
    return rows


def _live_checks(result: BracketResult) -> list:
    pool = result.pool
    checks = []
    mass = result.base.curve.total_mass
    checks.append(
        {
            "check": "Base payout probabilities sum to one",
            "passed": abs(mass - 1) < 1e-12,
            "detail": f"total probability {mass:.15f}",
        }
    )
    checks.append(
        {
            "check": "Lower bound does not exceed upper bound",
            "passed": result.lower_units <= result.upper_units,
            "detail": f"{format_usd(result.lower_units)} <= {format_usd(result.upper_units)}",
        }
    )
    checks.append(
        {
            "check": "Supporting scenario lies in the range and preserves marginal rates",
            "passed": result.shock_range.contains(result.witness.point)
            and not pool.infeasibilities(result.witness.point),
            "detail": _point(pool, result.witness.point)["label"],
        }
    )
    if result.registers.exact:
        gap = float(np.max(result.tracker.worst_tail - result.upper_envelope()))
        checks.append(
            {
                "check": "Certified bound is never below an observed feasible scenario",
                "passed": gap <= 1e-15,
                "detail": f"largest (observed - bound) {gap:.2e}",
            }
        )
    if result.registers.exact and pool.n <= 5:
        from .exact import ctmc_curve

        worst = 0.0
        for point in (pool.base_point, result.witness.point):
            exact = ctmc_curve(pool, pool.rates(point), result.registers.lower.support_units)
            curve = result.base.curve if point == pool.base_point else result.witness.curve
            worst = max(worst, float(np.max(np.abs(exact.tail - curve.tail))))
        checks.append(
            {
                "check": "Independent state-space solver agrees at the base and supporting "
                "scenarios",
                "passed": worst < 1e-12,
                "detail": f"max abs exceedance difference {worst:.2e}",
            }
        )
    else:
        checks.append(
            {
                "check": "Independent state-space solver",
                "passed": None,
                "detail": "not run: it supports exact payouts for at most five countries",
            }
        )
    return checks


def recorded_validation() -> dict:
    """Recorded validation and evaluator results shipped in results/."""
    folder = results_dir()
    status = {"validation": None, "benchmark": None}
    path = folder / "validation.json"
    if path.is_file():
        report = json.loads(path.read_text(encoding="utf-8"))
        status["validation"] = {
            "generated_utc": report["generated_utc"],
            "checks": report["summary"]["checks"],
            "passed": report["summary"]["passed"],
            "discrepancies": [
                {
                    "check": r["check"],
                    "target": r["proposal_target"],
                    "computed": r["computed_display"],
                    "note": r["note"],
                }
                for r in report["checks"]
                if r["status"] != "pass"
            ],
            "source": "results/validation.json",
        }
    path = folder / "benchmark" / "summary.json"
    if path.is_file():
        summary = json.loads(path.read_text(encoding="utf-8"))
        status["benchmark"] = {
            "protocol": "evaluation-v1",
            "execution": "classical_cpu",
            "totals": summary["totals"],
            "platform": summary.get("practice", {}).get("platform"),
            "repeats": summary.get("practice", {}).get("repeats"),
            "practice_protocol_eligible": summary.get("practice", {}).get("protocol_eligible"),
            "source": "results/benchmark/summary.json",
        }
    return status


def assumptions(result: BracketResult) -> list:
    pool = result.pool
    items = [
        f"Term of {pool.term_years:g} years. Earthquake and precipitation triggers are "
        "independent Poisson processes with stationary annual rates; perils are independent.",
        "Each earthquake trigger pays 1 to 4 quarter steps of the country's earthquake cap "
        "(severity mix from the public calibration); payments accumulate to the cap. "
        "Precipitation pays its full amount once over the term.",
        "Each neighbouring pair has its own shared earthquake and shared precipitation source. "
        "A shared earthquake triggers both neighbours with independent severity marks; "
        "a shared precipitation event triggers both payments.",
        "Each country's private rate equals its marginal rate minus the shared rates of its "
        "pairs, so marginal event frequencies are the same at every assumption in the range.",
        f"Range: shared earthquake rate {result.shock_range.earthquake_multiplier[0]:g} to "
        f"{result.shock_range.earthquake_multiplier[1]:g} times the base shared rate; shared "
        f"precipitation rate {100 * result.shock_range.rain_share[0]:g}% to "
        f"{100 * result.shock_range.rain_share[1]:g}% of the smaller neighbouring marginal rate. "
        "The same scalars apply to every pair.",
        f"Risk limit: probability that total payouts over the term are strictly greater than "
        f"the reserve is at most {100 * result.risk_limit:g}%. A payout exactly equal to the "
        "reserve is covered.",
        result.registers.description,
        f"Probabilities carry a {GUARD:g} floating-point allowance: a scenario within that "
        "distance of the limit does not decide a bound.",
    ]
    if pool.synthetic:
        items.insert(0, "SYNTHETIC example: illustrative inputs, not ADB or country data.")
    else:
        items.insert(
            0,
            "Public two-bond inputs from the organizer's benchmark calibration of ADB's 2026 "
            "Kyrgyz Republic and Tajikistan disaster relief bonds. The range and risk limit are "
            "demonstration inputs.",
        )
    return items


def calculate(payload: dict, with_table: bool = False) -> dict:
    """Run the engine for one analyst request and describe every result it shows.

    Identical requests reuse the stored report, so an export matches the
    calculation on screen (same timestamp) without recomputing it.
    """
    key = json.dumps(payload, sort_keys=True, default=str)
    report = _CACHE.get(key)
    if report is None:
        report = _compute(payload)
        _CACHE[key] = report
        while len(_CACHE) > _CACHE_SIZE:
            _CACHE.pop(next(iter(_CACHE)))
    report = copy.deepcopy(report)
    if not with_table:
        report.pop("exceedance_table")
    return report


def _compute(payload: dict) -> dict:
    parsed = parse_inputs(payload)
    pool, shock_range, limit = parsed["pool"], parsed["shock_range"], parsed["risk_limit"]
    registers = choose_registers(pool)
    result = bracket_reserve(
        pool,
        shock_range,
        limit,
        gap_usd=parsed["gap_usd"],
        max_evaluations=parsed["max_evaluations"],
        registers=registers,
    )
    grid = None
    if parsed["grid"]:
        grid_result = grid_reserve(
            pool, shock_range, limit, subdivisions=parsed["grid"], registers=registers
        )
        grid = {
            "method": grid_result.method,
            "evaluations": grid_result.total_evaluations,
            "lower": _usd(grid_result.lower_units),
            "upper": _usd(grid_result.upper_units),
            "resolved": grid_result.resolved,
        }
    evidence = result.shortfall_evidence()
    witness = result.witness
    binding = result.binding_cell()
    messages = []
    if not result.resolved:
        messages.append(
            {
                "level": "warning",
                "text": f"{STATUS_TEXT[result.status]} The smallest sufficient reserve lies "
                f"between {format_usd(result.lower_units)} and {format_usd(result.upper_units)}. "
                "Raise the evaluation budget or accept the upper amount.",
            }
        )
    if not registers.exact:
        messages.append({"level": "info", "text": registers.description})
    if not shock_range.contains(pool.base_point):
        messages.append(
            {
                "level": "info",
                "text": "The base assumptions lie outside the range, so they are reported but "
                "not used as a supporting scenario.",
            }
        )
    if parsed["modified"]:
        messages.append(
            {
                "level": "info",
                "text": "Country inputs or the term differ from the preloaded example.",
            }
        )
    shortfall = None
    if evidence:
        smaller, exceedance = evidence
        shortfall = {
            "smaller_reserve": _usd(smaller),
            "exceedance": exceedance,
            "expected_excess_usd": witness.curve.expected_excess(smaller),
            "explanation": (
                f"Under {_point(pool, witness.point)['label']}, total payouts exceed "
                f"{format_usd(smaller)} with probability {_pct(exceedance)}, above the "
                f"{100 * limit:g}% limit. Payouts take no value between {format_usd(smaller)} "
                f"and {format_usd(result.lower_units)}, so every smaller reserve falls short in "
                "this feasible scenario."
            ),
        }
    bound_at_upper = result.bound_at(result.upper_units)
    output = {
        "schema": "dlr-calculation-v1",
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "engine_version": __version__,
        "applicant": f"{APPLICANT} ({APPLICANT_ROLE})",
        "project": PROJECT_TITLE,
        "inputs": {
            "example": parsed["example"],
            "synthetic_countries": parsed["synthetic_countries"],
            "modified_from_example": parsed["modified"],
            "risk_limit": limit,
            "range": shock_range.to_dict(),
            "gap_tolerance_usd": parsed["gap_usd"],
            "evaluation_budget": parsed["max_evaluations"],
            "grid_subdivisions": parsed["grid"],
            "pool": pool.to_dict(),
        },
        "status": result.status,
        "status_text": STATUS_TEXT[result.status],
        "resolved": result.resolved,
        "messages": messages,
        "payout_register": registers.to_dict(),
        "base": {
            "reserve": _usd(result.base.lower_units),
            "reserve_upper": _usd(result.base_upper_units),
            "exceedance_at_reserve": result.base.curve.exceedance(result.base.lower_units),
            "expected_excess_usd": result.base.curve.expected_excess(result.base.lower_units),
            "point": _point(pool, pool.base_point),
        },
        "range_result": {
            "lower": _usd(result.lower_units),
            "upper": _usd(result.upper_units),
            "gap": _usd(result.upper_units - result.lower_units),
            "resolved": result.resolved,
        },
        "supporting_scenario": {
            "role": witness.role,
            "point": _point(pool, witness.point),
            "private_rates": _rates(pool, pool.rates(witness.point)),
            "exceedance_at_lower": witness.curve.exceedance(result.lower_units),
            "shortfall": shortfall,
        },
        "sufficiency": {
            "cells": len(result.leaves),
            "bound_at_upper": bound_at_upper,
            "binding_cell": {
                "earthquake_multiplier": [float(binding.low[0]), float(binding.high[0])],
                "rain_share": [float(binding.low[1]), float(binding.high[1])],
                "dominating_rates": pool.dominating_rates(binding.low, binding.high).to_dict(),
            },
            "explanation": (
                f"The range was divided into {_cells(len(result.leaves))}. For each cell a "
                "dominating model uses the cell's highest shared rates and highest private "
                "rates. Every feasible assumption in the cell is a thinning of that model, and "
                "capped payouts never fall when events are added, so its exceedance is an upper "
                f"bound for the whole cell. At {format_usd(result.upper_units)} the largest cell "
                f"bound is {_pct(bound_at_upper)}, within the {100 * limit:g}% limit, so this "
                "reserve is sufficient across the range."
            ),
        },
        "key_amounts": _key_amounts(result),
        "evaluations": {
            "adaptive": {**result.evaluations, "total": result.total_evaluations},
            "budget": result.max_evaluations,
            "grid": grid,
            "counting_rule": "One evaluation is one aggregate payout distribution (one tensor "
            "contraction).",
        },
        "chart": _chart(result),
        "headline": _headline(result, grid),
        "insights": _insights(result, registers),
        "assumptions": assumptions(result),
        "checks": _live_checks(result),
        "recorded_validation": recorded_validation(),
    }
    output["exceedance_table"] = _table(result)
    return output


def _headline(result: BracketResult, grid: dict | None) -> dict:
    """Numbers for the verdict card."""
    corners = len(result.shock_range.corners())
    grid_evaluations = grid["evaluations"] if grid else 1 + corners + 2 * 16 * 16
    uplift = result.upper_units - result.base.lower_units
    base_value = result.base.lower_units / 400
    return {
        "reserve": _usd(result.upper_units),
        "lower": _usd(result.lower_units),
        "base": _usd(result.base.lower_units),
        "uplift": _usd(abs(uplift)),
        "uplift_sign": 1 if uplift > 0 else (-1 if uplift < 0 else 0),
        "uplift_percent": (uplift / 400) / base_value * 100 if base_value else None,
        "evaluations": result.total_evaluations,
        "grid_evaluations": grid_evaluations,
        "grid_measured": grid is not None,
        "evaluations_saved_percent": 100 * (1 - result.total_evaluations / grid_evaluations),
        "term_years": result.pool.term_years,
        "risk_limit": result.risk_limit,
    }


def _insights(result: BracketResult, registers) -> dict:
    """Assumption map, shortfall drivers and the regional chain for the dashboard."""
    pool = result.pool
    grid = assumption_map(
        pool,
        result.shock_range,
        result.risk_limit,
        registers.lower,
        result.upper_units,
        map_resolution(pool.n),
    )
    evidence_amount = result.shortfall_evidence()
    amount = evidence_amount[0] if evidence_amount else result.upper_units
    drivers = shortfall_drivers(pool, pool.rates(result.witness.point), registers.lower, amount)
    return {
        "map": {
            **grid,
            "cells": [
                {
                    "earthquake_multiplier": [float(c.low[0]), float(c.high[0])],
                    "rain_share": [float(c.low[1]), float(c.high[1])],
                    "upper_usd": c.upper_units / 400,
                }
                for c in result.leaves
            ],
            "evaluated_points": [list(p) for p in result.tracker.points],
            "witness": list(result.witness.point),
            "base": [float(x) for x in pool.base_point],
            "base_in_range": result.shock_range.contains(pool.base_point),
            "note": "Point reserve at each assumption on the grid. Map evaluations explain the "
            "result and are not part of the certified bounds.",
        },
        "drivers": {**drivers, "scenario": _point(pool, result.witness.point)["label"]},
        "chain": chain_graph(pool, result.shock_range, result.witness.point),
        "evaluations": {"map": grid["evaluations"], "drivers": drivers["evaluations"]},
    }


def _limits(payload: dict) -> list:
    raw = payload.get("limits_percent") or list(DEFAULT_SWEEP_PERCENT)
    if not isinstance(raw, list) or not 1 <= len(raw) <= 12:
        raise InputError("give between 1 and 12 risk limits to compare")
    limits = []
    for value in raw:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise InputError("risk limits must be numbers")
        if not math.isfinite(value) or not 0 < value < 100:
            raise InputError("each risk limit must be strictly between 0% and 100%")
        limits.append(float(value) / 100)
    return sorted(set(limits))


def _remember(key: str, report: dict):
    _CACHE[key] = report
    while len(_CACHE) > _CACHE_SIZE:
        _CACHE.pop(next(iter(_CACHE)))


def sweep(payload: dict) -> dict:
    """Reserve bracket for several risk limits with the same pool and range."""
    parsed = parse_inputs(payload)
    limits = _limits(payload)
    key = json.dumps({"sweep": payload, "limits": limits}, sort_keys=True, default=str)
    if key not in _CACHE:
        registers = choose_registers(parsed["pool"])
        rows = limit_sweep(
            parsed["pool"],
            parsed["shock_range"],
            limits,
            registers,
            gap_usd=parsed["gap_usd"],
            max_evaluations=parsed["max_evaluations"],
        )
        _remember(
            key,
            {
                "rows": rows,
                "current_limit": parsed["risk_limit"],
                "total_evaluations": sum(r["evaluations"] for r in rows),
                "pool": parsed["pool"].name,
                "synthetic": parsed["pool"].synthetic,
            },
        )
    return copy.deepcopy(_CACHE[key])


def quantum_check(payload: dict) -> dict:
    """Amplitude-estimation check of P(L > base reserve) at the current base assumptions."""
    from .quantum import queries_for_precision, query_study

    parsed = parse_inputs(payload)
    key = json.dumps({"quantum": payload}, sort_keys=True, default=str)
    if key in _CACHE:
        return copy.deepcopy(_CACHE[key])
    pool = parsed["pool"]
    registers = choose_registers(pool)
    curve = distribution(pool, pool.rates(pool.base_point), registers.lower)
    amount = curve.reserve_units(parsed["risk_limit"])
    if curve.exceedance(amount) <= 0:
        raise InputError("the base reserve is never exceeded, so there is nothing to estimate")
    study = query_study(curve, amount, schedules=8, repeats=60)
    report = {
        **study,
        "pool": pool.name,
        "synthetic": pool.synthetic,
        "exact_from_contraction": curve.exceedance(amount),
        "precision": [queries_for_precision(study["probability"], e) for e in (1e-3, 1e-4)],
        "rounded_payouts": not registers.exact,
        "scope": "Ideal noiseless statevector simulation on the CPU. Loading the payout law "
        "uses the classical contraction, so this checks the estimator and its query scaling; "
        "it is not a speed-up claim.",
    }
    _remember(key, report)
    return copy.deepcopy(report)


@lru_cache(maxsize=1)
def _complexity() -> tuple:
    rows = []
    for n in (2, 5, 10, 20, 50):
        levels = choose_registers(synthetic_chain(n)).lower.size
        entries = 2 * 10 * 10 + max(n - 2, 0) * 10 * 10 * 10
        rows.append(
            {
                "countries": n,
                "joint_country_states": f"10^{n}",
                "payout_levels": levels,
                "tensor_entries": entries,
                "register_operations": entries * levels,
            }
        )
    return tuple(rows)


def evidence() -> dict:
    """Recorded validation, evaluator and quantum results plus contraction scaling."""
    folder = results_dir()

    def read(name):
        path = folder / name
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None

    return {
        "validation": read("validation.json"),
        "benchmark": read("benchmark/summary.json"),
        "quantum": read("quantum.json"),
        "complexity": list(_complexity()),
        "applicant": f"{APPLICANT} ({APPLICANT_ROLE})",
    }


# ---------------------------------------------------------------------------
# Export


def export(payload: dict, fmt: str = "json"):
    """Return (filename, content type, bytes) for a report of this calculation."""
    if fmt not in ("json", "md"):
        raise InputError("export format must be json or md")
    report = calculate(payload, with_table=True)
    report.pop("chart")
    stamp = report["generated_utc"].replace(":", "").replace("-", "")[:15]
    name = f"reserve-report-{report['inputs']['example']}-{stamp}"
    if fmt == "json":
        body = json.dumps(report, indent=2, allow_nan=False) + "\n"
        return f"{name}.json", "application/json", body.encode("utf-8")
    return f"{name}.md", "text/markdown; charset=utf-8", to_markdown(report).encode("utf-8")


def _table(result: BracketResult) -> list:
    """Exceedance at every payout level where any of the three curves steps."""
    support = result.registers.lower.support_units
    base, observed = result.base.curve.tail, result.tracker.worst_tail
    # With exact payouts both registers share one support, so the envelope
    # lines up index by index; rounded registers need a lookup per amount.
    envelope = result.upper_envelope() if result.registers.exact else None
    rows, previous = [], None
    for j, units in enumerate(support):
        bound = float(envelope[j]) if envelope is not None else result.bound_at(int(units))
        values = (float(base[j]), float(observed[j]), bound)
        if values != previous:
            rows.append(
                {
                    "amount_usd": decimal_string(int(units)),
                    "base_exceedance": values[0],
                    "highest_feasible_exceedance": values[1],
                    "certified_bound": values[2],
                }
            )
        previous = values
    return rows


def _grid_text(grid: dict) -> str:
    result = (
        grid["upper"]["text"]
        if grid["resolved"]
        else f"{grid['lower']['text']} to {grid['upper']['text']}"
    )
    return f"; {grid['evaluations']} for the {grid['method']} ({result})"


def to_markdown(report: dict) -> str:
    inputs = report["inputs"]
    pool = inputs["pool"]
    rng = inputs["range"]
    base, outcome = report["base"], report["range_result"]
    scenario = report["supporting_scenario"]
    lines = [
        f"# Reserve report: {pool['name']}",
        "",
        f"{report['project']}. Prepared with the reserve engine {report['engine_version']} "
        f"on {report['generated_utc']}. Applicant: {report['applicant']}.",
        "",
    ]
    if pool["synthetic"]:
        lines += ["> SYNTHETIC example: illustrative inputs, not ADB or country data.", ""]
    lines += [
        "## Result",
        "",
        f"- Risk limit: {100 * inputs['risk_limit']:g}% probability that total payouts over "
        f"{pool['term_years']} years are strictly greater than the reserve",
        f"- Base reserve: {base['reserve']['text']}"
        + (
            f" to {base['reserve_upper']['text']} (rounding)"
            if base["reserve_upper"]["usd"] != base["reserve"]["usd"]
            else ""
        ),
        "- Reserve across the range: "
        + (
            outcome["upper"]["text"]
            if outcome["resolved"]
            else f"between {outcome['lower']['text']} and {outcome['upper']['text']}"
        ),
        f"- Status: {report['status_text']}",
        f"- Distribution evaluations: {report['evaluations']['adaptive']['total']} (adaptive)"
        + (_grid_text(report["evaluations"]["grid"]) if report["evaluations"]["grid"] else ""),
        "",
        "## Why a smaller reserve falls short",
        "",
        scenario["shortfall"]["explanation"]
        if scenario["shortfall"]
        else "A zero reserve meets the limit in every evaluated scenario.",
        "",
        "## Why the upper amount is sufficient",
        "",
        report["sufficiency"]["explanation"],
        "",
        "## Inputs",
        "",
        f"Shared earthquake multiplier: {rng['earthquake_multiplier'][0]:g} to "
        f"{rng['earthquake_multiplier'][1]:g}. Shared rain share: "
        f"{100 * rng['rain_share'][0]:g}% to {100 * rng['rain_share'][1]:g}%. Base point: "
        f"{pool['base_point']['earthquake_multiplier']:g} and "
        f"{100 * pool['base_point']['rain_share']:g}%.",
        "",
        "| Country | Earthquake rate / yr | Rain rate / yr | Earthquake cap | Rain payment |",
        "|---|---|---|---|---|",
    ]
    for c in pool["countries"]:
        lines.append(
            f"| {c['code']} | {c['earthquake_rate_per_year']:.6f} | {c['rain_rate_per_year']:.6f}"
            f" | {c['earthquake_cap']} | {c['rain_payment']} |"
        )
    lines += [
        "",
        "## Key amounts",
        "",
        "| Amount | Base P(L > amount) | Highest feasible | Certified bound |",
        "|---|---|---|---|",
    ]
    for row in report["key_amounts"]:
        lines.append(
            f"| {row['amount']['text']} | {_pct(row['base_exceedance'])} | "
            f"{_pct(row['observed_exceedance'])} | {_pct(row['bound_exceedance'])} |"
        )
    lines += ["", "## Assumptions", ""] + [f"- {a}" for a in report["assumptions"]]
    lines += ["", "## Validation status", ""]
    for check in report["checks"]:
        mark = {True: "pass", False: "FAIL", None: "not run"}[check["passed"]]
        lines.append(f"- {check['check']}: {mark} ({check['detail']})")
    recorded = report["recorded_validation"]
    if recorded["validation"]:
        v = recorded["validation"]
        lines.append(
            f"- Recorded validation ({v['generated_utc']}): {v['passed']} of {v['checks']} "
            "checks agree with the proposal targets"
        )
        for d in v["discrepancies"]:
            lines.append(
                f"  - Discrepancy: {d['check']}: target {d['target']}, computed {d['computed']}"
            )
    if recorded["benchmark"]:
        t = recorded["benchmark"]["totals"]
        lines.append(
            "- Official evaluation-v1 evaluator (classical_cpu): "
            f"{t['cases_passing']}/{t['cases']} cases and "
            f"{t['metric_passes']}/{t['metric_checks']} metric checks pass"
        )
    lines.append("")
    return "\n".join(lines)
