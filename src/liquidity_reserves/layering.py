"""Risk-layering optimizer: the cheapest way to fund the certified reserve.

The certified reserve R* fixes how much money must be available for the
pool's shortfall chance to stay within the risk limit across the whole range.
This module decides how to arrange that capacity across three instruments,
stacked in the order the World Bank's sovereign framework shows is cheapest
(Clarke, Mahul, Poulter and Teh 2016; Geneva Papers 2017):

    cash reserve          pays [0, a1)
    contingent credit     pays [a1, a2)
    parametric insurance  pays [a2, R*)

Expected cost of funding the term's payouts X up to R* (all in USD over the term):

    reserve    E[min(X, a1)] + rho * T * E[a1 - min(X, a1)]       (paid losses + cost of idle cash)
    credit     delta * E[layer] + (kappa + (phi + beta) * T) * (a2 - a1)
               (drawn dollars at present-value cost, fees, and the use of cheap borrowing room)
    insurance  m * E[layer]                     (premium = multiple x expected loss)

With g(a) = E[max(X - a, 0)], every layer expectation is a difference of g at
its ends, so the cost splits into a term in a1 and a term in a2 under the
ordering a1 <= a2 <= R*. The exact optimum over every attainable payout level
is a prefix-minimum scan. The same problem is also written as a QUBO (one-hot
choice of each breakpoint, ordering penalty) and solved with QAOA in qaoa.py.

Expected costs use the base assumptions by default (how a market prices
cover) or the certified upper bound across the range ("bound"), which caps
the expected cost for every assumption in the range.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

from .bounds import bracket_reserve
from .contraction import choose_registers
from .money import UNITS_PER_USD
from .portfolio import InputError

SOURCES = {
    "reserve_cost": "Clarke, Mahul, Poulter & Teh (2016), World Bank WPS 7721: (i - r)/(1 + i) "
    "with i = 5.5%, r = 1%",
    "credit_fees": "ADB Contingent Disaster Financing, Crisis Preparedness and Response Toolkit "
    "(2026): 0.25% front-end fee, 0.25% a year on the undrawn balance",
    "credit_drawn": "Clarke et al. (2016) case study: (1 + c)/(1 + i) with c = 2.5%, i = 5.5%",
    "credit_capacity": "Clarke et al. (2016): beta (i - c)/(1 + i) per dollar arranged, beta = 1",
    "insurance_multiple": "ADB 2026 Kyrgyz Republic disaster relief bond: 6.00% risk margin over "
    "2.296% expected loss (Artemis deal directory)",
}


@dataclass(frozen=True)
class CostParameters:
    reserve_cost_per_year: float = 0.043  # opportunity cost of each dollar held
    credit_front_end_fee: float = 0.0025
    credit_fee_per_year: float = 0.0025  # on the undrawn balance
    credit_drawn_cost: float = 0.972  # present-value cost of repaying a drawn dollar
    credit_capacity_charge_per_year: float = 0.0284  # use of cheap borrowing room
    insurance_multiple: float = 2.6  # premium / expected layer loss
    credit_limit_usd: float | None = None  # largest credit line available, if capped

    def validate(self):
        problems = []
        for name, value, low, high in (
            ("reserve cost per year", self.reserve_cost_per_year, 0.0, 1.0),
            ("credit front-end fee", self.credit_front_end_fee, 0.0, 1.0),
            ("credit fee per year", self.credit_fee_per_year, 0.0, 1.0),
            ("credit drawn cost", self.credit_drawn_cost, 0.0, 5.0),
            ("credit capacity charge per year", self.credit_capacity_charge_per_year, 0.0, 1.0),
            ("insurance multiple", self.insurance_multiple, 0.0, 20.0),
        ):
            if not np.isfinite(value) or not low <= value <= high:
                problems.append(f"{name} must be between {low:g} and {high:g}")
        if self.credit_limit_usd is not None and not (
            np.isfinite(self.credit_limit_usd) and self.credit_limit_usd >= 0
        ):
            problems.append("credit limit must be zero or a positive amount")
        if problems:
            raise InputError(problems)
        return self

    @property
    def credit_capacity_fee(self) -> float:
        """Per dollar of credit arranged, over a term of one year (scaled by the term)."""
        return self.credit_fee_per_year + self.credit_capacity_charge_per_year

    def to_dict(self) -> dict:
        return asdict(self)


def excess_curve(support_units: np.ndarray, tail: np.ndarray) -> np.ndarray:
    """g(a) = E[max(X - a, 0)] in USD at each support point, from a tail P(X > a).

    The tail is constant between support points, so g is the area under it.
    Using a certified tail envelope gives an upper bound on g for every
    assumption the envelope covers.
    """
    widths = np.diff(support_units.astype(float)) / UNITS_PER_USD
    pieces = np.asarray(tail[:-1], dtype=float) * widths
    return np.r_[np.cumsum(pieces[::-1])[::-1], 0.0]


def _capacity_fee(cost: CostParameters, term: float) -> float:
    return cost.credit_front_end_fee + cost.credit_capacity_fee * term


def _layer_costs(
    g: np.ndarray, levels_usd: np.ndarray, top: int, term: float, cost: CostParameters
):
    """Cost terms that depend only on a1 (h1) and only on a2 (h2)."""
    g0, g_top = g[0], g[top]
    hold = cost.reserve_cost_per_year * term
    fee = _capacity_fee(cost, term)
    h1 = (1 - hold) * (g0 - g) + hold * levels_usd + cost.credit_drawn_cost * g - fee * levels_usd
    h2 = -cost.credit_drawn_cost * g + fee * levels_usd + cost.insurance_multiple * (g - g_top)
    return h1, h2


def plan_cost(g, levels_usd, i1, i2, top, term, cost: CostParameters) -> dict:
    a1, a2, a3 = levels_usd[i1], levels_usd[i2], levels_usd[top]
    reserve_paid = g[0] - g[i1]
    credit_drawn = g[i1] - g[i2]
    insured = g[i2] - g[top]
    hold = cost.reserve_cost_per_year * term
    parts = {
        "reserve": reserve_paid + hold * (a1 - reserve_paid),
        "credit": cost.credit_drawn_cost * credit_drawn + _capacity_fee(cost, term) * (a2 - a1),
        "insurance": cost.insurance_multiple * insured,
    }
    return {
        "layers_usd": {"reserve": [0.0, a1], "credit": [a1, a2], "insurance": [a2, a3]},
        "expected_payout_usd": {
            "reserve": reserve_paid,
            "credit": credit_drawn,
            "insurance": insured,
        },
        "cost_usd": parts,
        "total_cost_usd": float(sum(parts.values())),
    }


def optimize_layers(
    g: np.ndarray, levels_usd: np.ndarray, top: int, term: float, cost: CostParameters
):
    """Exact minimum over every attainable a1 <= a2 <= levels[top].

    Without a credit cap the a1 and a2 terms separate, so a prefix minimum is
    exact. With a cap (a2 - a1 <= limit) every admissible pair is checked.
    """
    h1, h2 = _layer_costs(g, levels_usd, top, term, cost)
    h1, h2, levels = h1[: top + 1], h2[: top + 1], levels_usd[: top + 1]
    if cost.credit_limit_usd is None:
        best_prefix = np.minimum.accumulate(h1)
        arg_prefix = np.zeros(len(h1), dtype=int)
        for k in range(1, len(h1)):
            arg_prefix[k] = k if h1[k] < best_prefix[k - 1] else arg_prefix[k - 1]
        i2 = int(np.argmin(best_prefix + h2))
        return int(arg_prefix[i2]), i2
    total = h1[:, None] + h2[None, :]
    gap = levels[None, :] - levels[:, None]
    admissible = (gap >= 0) & (gap <= cost.credit_limit_usd + 1e-6)
    total = np.where(admissible, total, np.inf)
    i1, i2 = np.unravel_index(int(np.argmin(total)), total.shape)
    return int(i1), int(i2)


def risk_layering(
    pool,
    shock_range,
    risk_limit: float,
    cost: CostParameters | None = None,
    basis: str = "base",
    result=None,
) -> dict:
    """Cheapest reserve / credit / insurance stack that funds the certified reserve."""
    cost = (cost or CostParameters()).validate()
    if basis not in ("base", "bound"):
        raise InputError("cost basis must be 'base' or 'bound'")
    registers = choose_registers(pool)
    result = result or bracket_reserve(pool, shock_range, risk_limit, registers=registers)
    if basis == "base":
        support, tail = result.base_upper_curve.support_units, result.base_upper_curve.tail
    else:
        support, tail = result.registers.upper.support_units, result.upper_envelope()
    levels = support.astype(float) / UNITS_PER_USD
    top = int(np.searchsorted(support, result.upper_units))
    if top >= len(support) or support[top] != result.upper_units:
        top = int(np.searchsorted(support, result.upper_units, side="right")) - 1
    g = excess_curve(support, tail)
    term = float(pool.term_years)
    i1, i2 = optimize_layers(g, levels, top, term, cost)
    best = plan_cost(g, levels, i1, i2, top, term, cost)
    alternatives = {
        "all_reserve": plan_cost(g, levels, top, top, top, term, cost),
        "reserve_and_insurance": _best_two(g, levels, top, term, cost, without="credit"),
        "credit_and_insurance": _best_two(g, levels, top, term, cost, without="reserve"),
        "all_insurance": plan_cost(g, levels, 0, 0, top, term, cost),
    }
    saving = alternatives["all_reserve"]["total_cost_usd"] - best["total_cost_usd"]
    return {
        "pool": pool.name,
        "risk_limit": risk_limit,
        "term_years": term,
        "capacity_usd": float(levels[top]),
        "capacity_status": result.status,
        "basis": basis,
        "parameters": cost.to_dict(),
        "sources": SOURCES,
        "optimal": best,
        "alternatives": alternatives,
        "saving_vs_all_reserve_usd": float(saving),
        "saving_vs_all_reserve_percent": float(
            100 * saving / alternatives["all_reserve"]["total_cost_usd"]
        ),
        "candidates": int(top + 1),
        "method": "exact scan over every attainable breakpoint (prefix minimum)",
    }


def _best_two(g, levels, top, term, cost, without: str) -> dict:
    """Best plan when one instrument is unavailable."""
    best = None
    for i in range(top + 1):
        i1, i2 = (i, i) if without == "credit" else (0, i)
        if (
            without == "reserve"
            and cost.credit_limit_usd is not None
            and levels[i] > cost.credit_limit_usd + 1e-6
        ):
            continue
        plan = plan_cost(g, levels, i1, i2, top, term, cost)
        if best is None or plan["total_cost_usd"] < best["total_cost_usd"]:
            best = plan
    return best


def frontier(
    pool,
    shock_range,
    limits=(0.005, 0.0075, 0.01, 0.015, 0.02, 0.03, 0.05),
    cost: CostParameters | None = None,
) -> list:
    """Optimal funding cost against the risk limit."""
    rows = []
    for limit in limits:
        plan = risk_layering(pool, shock_range, limit, cost)
        rows.append(
            {
                "risk_limit": limit,
                "capacity_usd": plan["capacity_usd"],
                "optimal_cost_usd": plan["optimal"]["total_cost_usd"],
                "all_reserve_cost_usd": plan["alternatives"]["all_reserve"]["total_cost_usd"],
                "layers_usd": plan["optimal"]["layers_usd"],
            }
        )
    return rows


def main(argv=None) -> int:
    """Record the optimizer, pooling and QAOA results in results/layering.json."""
    import json

    from .examples import DEFAULT_RANGE, public_two_bond, synthetic_chain
    from .paths import results_dir
    from .pooling import pooling_benefit

    public, five = public_two_bond(), synthetic_chain(5)
    record = {
        "schema": "dlr-layering-v1",
        "sources": SOURCES,
        "defaults": CostParameters().to_dict(),
    }
    record["plans"] = [
        risk_layering(pool, DEFAULT_RANGE, limit, basis=basis)
        for pool in (public, five)
        for limit in (0.015, 0.005)
        for basis in ("base", "bound")
    ]
    record["sensitivity"] = []
    for label, params in (
        ("insurance multiple 1.5", {"insurance_multiple": 1.5}),
        ("insurance multiple 4.5", {"insurance_multiple": 4.5}),
        ("credit line capped at $40m", {"credit_limit_usd": 40e6}),
        ("no credit capacity charge", {"credit_capacity_charge_per_year": 0.0}),
    ):
        plan = risk_layering(public, DEFAULT_RANGE, 0.015, CostParameters(**params))
        record["sensitivity"].append(
            {
                "case": label,
                "layers_usd": plan["optimal"]["layers_usd"],
                "total_cost_usd": plan["optimal"]["total_cost_usd"],
                "saving_vs_all_reserve_percent": plan["saving_vs_all_reserve_percent"],
            }
        )
    record["frontier"] = frontier(public, DEFAULT_RANGE)
    record["pooling"] = [
        pooling_benefit(pool, DEFAULT_RANGE, limit)
        for pool in (public, five)
        for limit in (0.015, 0.005)
    ]
    try:
        from .qaoa import layering_qaoa, plan_inputs

        inputs = plan_inputs(public, DEFAULT_RANGE, 0.015)
        record["qaoa"] = [layering_qaoa(inputs, k=6, reps=3, mixer=mixer) for mixer in ("xy", "x")]
    except ImportError:
        record["qaoa"] = None
    out = results_dir() / "layering.json"
    out.write_text(json.dumps(record, indent=2, default=float) + "\n", encoding="utf-8")
    best = record["plans"][0]
    print(
        f"Public 1.5%: {best['optimal']['layers_usd']}, "
        f"cost ${best['optimal']['total_cost_usd']:,.0f}, "
        f"{best['saving_vs_all_reserve_percent']:.1f}% below all-cash"
    )
    for row in record["pooling"]:
        print(
            f"Pooling {row['pool'][:22]} {row['risk_limit']:.1%}: "
            f"{row['saving_percent']:.1f}% saving"
        )
    for row in record["qaoa"] or []:
        print(
            f"QAOA {row['mixer']}: optimum found {row['qaoa_finds_optimum']}, "
            f"P(optimal) {row['probability_optimal_sampled']:.3f}"
        )
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
