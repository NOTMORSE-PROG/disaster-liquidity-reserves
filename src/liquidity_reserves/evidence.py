"""Evidence for the shared-shock range from the World Bank event loss tables.

The bond documents give one same-event trigger probability per country pair.
The organizer's benchmark also ships an independent source: the World Bank
SFRARR Central Asia seismic and flood event loss tables (CC BY 4.0). Each row
is a catalogue event with an annual rate and a mean loss per country.

To compare the two sources on the same footing, each country's trigger is
calibrated so that catalogue events reach it at the bond's annual trigger rate
(the highest-loss events are taken until their rates add up to that rate).
The shared rate is then the total rate of events that trigger both countries.
Every catalogue event has the same rate, so the shared rate is a count times
that rate and gets an exact Poisson interval for catalogue sampling.

Secondary uncertainty (each event's loss standard deviation) is a sensitivity:
a gamma loss per event with the catalogue mean and standard deviation, the
trigger recalibrated to the same expected rate, and the two countries treated
as independent or comonotone given the event.

None of this changes the certified bounds. It tells the analyst which range of
shared rates the available evidence supports.
"""

from __future__ import annotations

import json
from functools import lru_cache

import numpy as np
from scipy.optimize import brentq
from scipy.stats import chi2, gamma

from .examples import public_two_bond
from .paths import benchmark_dir, results_dir
from .portfolio import SharedShockRange

PERIL_FILES = {"earthquake": "seismic.npz", "rain": "flood.npz"}
CONFIDENCE = 0.95
SOURCE = (
    "World Bank SFRARR Central Asia seismic and flood event loss tables (CC BY 4.0), "
    "as shipped in the organizer's benchmark data folder"
)


@lru_cache(maxsize=2)
def load_catalogue(peril: str) -> dict:
    """Annual rates, mean losses and loss standard deviations for one peril."""
    if peril not in PERIL_FILES:
        raise ValueError("peril must be 'earthquake' or 'rain'")
    with np.load(benchmark_dir() / "data" / PERIL_FILES[peril], allow_pickle=False) as data:
        return {
            "rate": np.asarray(data["annual_rate"], dtype=float),
            "loss": np.asarray(data["loss_usd"], dtype=float),
            "sd": np.asarray(data["sd_usd"], dtype=float),
            "countries": [str(c) for c in data["countries"]],
        }


def _column(catalogue: dict, code: str) -> int:
    try:
        return catalogue["countries"].index(code)
    except ValueError:
        raise ValueError(f"{code} is not in the catalogue") from None


def trigger_set(rate: np.ndarray, loss: np.ndarray, target_rate: float):
    """Highest-loss events whose rates first reach target_rate.

    Returns (mask, threshold_usd, attained_rate). Ties at the threshold are all
    included, so the attained rate can exceed the target by one tie group.
    """
    if not target_rate > 0:
        raise ValueError("target trigger rate must be positive")
    order = np.argsort(-loss, kind="stable")
    cumulative = np.cumsum(rate[order])
    k = int(np.searchsorted(cumulative, target_rate * (1 - 1e-12)))
    k = min(k, len(order) - 1)
    threshold = float(loss[order[k]])
    mask = loss >= threshold
    return mask, threshold, float(rate[mask].sum())


def poisson_interval(count: int, confidence: float = CONFIDENCE):
    """Exact (Garwood) interval for a Poisson mean given an observed count."""
    alpha = 1 - confidence
    low = 0.0 if count == 0 else chi2.ppf(alpha / 2, 2 * count) / 2
    high = chi2.ppf(1 - alpha / 2, 2 * count + 2) / 2
    return float(low), float(high)


def _gamma_trigger(loss, sd, threshold):
    """P(event loss >= threshold) with a gamma loss of the catalogue mean and sd."""
    p = np.zeros_like(loss)
    positive = loss > 0
    spread = np.maximum(sd[positive], 1e-9 * loss[positive])
    shape = (loss[positive] / spread) ** 2
    scale = spread**2 / loss[positive]
    p[positive] = gamma.sf(threshold, a=shape, scale=scale)
    return p


def _secondary(rate, loss, sd, target_rate):
    upper = float(loss.max()) * 50 + 1.0

    def gap(u):
        return float(rate @ _gamma_trigger(loss, sd, u)) - target_rate

    threshold = brentq(gap, 1e-6, upper, xtol=1e-3, rtol=1e-10)
    return threshold, _gamma_trigger(loss, sd, threshold)


def pair_dependence(
    peril: str, a: str, b: str, rate_a: float, rate_b: float, secondary: bool = True
) -> dict:
    """Same-event triggering of countries a and b at their bond trigger rates."""
    catalogue = load_catalogue(peril)
    rate = catalogue["rate"]
    ia, ib = _column(catalogue, a), _column(catalogue, b)
    loss_a, loss_b = catalogue["loss"][:, ia], catalogue["loss"][:, ib]
    mask_a, thr_a, got_a = trigger_set(rate, loss_a, rate_a)
    mask_b, thr_b, got_b = trigger_set(rate, loss_b, rate_b)
    both = mask_a & mask_b
    count = int(both.sum())
    event_rates = np.unique(rate)
    equal_rates = len(event_rates) == 1
    shared = float(rate[both].sum())
    row = {
        "peril": peril,
        "pair": f"{a}-{b}",
        "events_in_catalogue": int(len(rate)),
        "event_rate_per_year": float(event_rates[0]) if equal_rates else None,
        "target_trigger_rate_per_year": {a: float(rate_a), b: float(rate_b)},
        "attained_trigger_rate_per_year": {a: got_a, b: got_b},
        "trigger_loss_usd": {a: thr_a, b: thr_b},
        "trigger_events": {a: int(mask_a.sum()), b: int(mask_b.sum())},
        "joint_trigger_events": count,
        "shared_rate_per_year": shared,
        "conditional_trigger": {f"{b}_given_{a}": shared / got_a, f"{a}_given_{b}": shared / got_b},
    }
    if equal_rates:
        low, high = poisson_interval(count)
        row["shared_rate_interval_per_year"] = [low * event_rates[0], high * event_rates[0]]
        row["interval"] = (
            f"exact Poisson {int(CONFIDENCE * 100)}% interval on the joint event count"
        )
    if secondary:
        sd_a, sd_b = catalogue["sd"][:, ia], catalogue["sd"][:, ib]
        _, p_a = _secondary(rate, loss_a, sd_a, rate_a)
        _, p_b = _secondary(rate, loss_b, sd_b, rate_b)
        row["secondary_uncertainty"] = {
            "independent_given_event_per_year": float(rate @ (p_a * p_b)),
            "comonotone_given_event_per_year": float(rate @ np.minimum(p_a, p_b)),
            "model": "gamma loss per event with the catalogue mean and standard deviation",
        }
    return row


def tail_dependence(peril: str, a: str, b: str, return_periods=(2, 5, 10, 25, 50, 100)) -> list:
    """Same-event conditional trigger probability as the trigger gets rarer."""
    catalogue = load_catalogue(peril)
    rate = catalogue["rate"]
    ia, ib = _column(catalogue, a), _column(catalogue, b)
    rows = []
    for period in return_periods:
        target = 1.0 / period
        mask_a, thr_a, got_a = trigger_set(rate, catalogue["loss"][:, ia], target)
        mask_b, thr_b, _ = trigger_set(rate, catalogue["loss"][:, ib], target)
        shared = float(rate[mask_a & mask_b].sum())
        rows.append(
            {
                "return_period_years": period,
                "trigger_loss_usd": {a: thr_a, b: thr_b},
                "joint_trigger_events": int((mask_a & mask_b).sum()),
                "conditional_trigger": shared / got_a,
            }
        )
    return rows


def public_evidence(pool=None) -> dict:
    """Compare the bond's base shared rates with the catalogue for the public example."""
    pool = pool or public_two_bond()
    a, b = pool.codes
    base_shared = float(pool.base_shared_earthquake[0])
    rain_min = float(pool.rain_pair_minimum[0])
    quake = pair_dependence("earthquake", a, b, *pool.earthquake_marginal)
    rain = pair_dependence("rain", a, b, *pool.rain_marginal)

    def multiplier(rate):
        return rate / base_shared

    def share(rate):
        return rate / rain_min

    _, q_high = quake["shared_rate_interval_per_year"]
    _, r_high = rain["shared_rate_interval_per_year"]
    quake_sources = [
        {
            "source": "World Bank catalogue, central estimate",
            "value": multiplier(quake["shared_rate_per_year"]),
        },
        {"source": "World Bank catalogue, 95% upper", "value": multiplier(q_high)},
        {
            "source": "World Bank catalogue, secondary uncertainty (comonotone)",
            "value": multiplier(quake["secondary_uncertainty"]["comonotone_given_event_per_year"]),
        },
        {"source": "Bond documents, same-event conditionals (base)", "value": 1.0},
        {"source": "Organizer common-shock stress scenario", "value": 2.0},
    ]
    rain_sources = [
        {
            "source": "World Bank catalogue, central estimate",
            "value": share(rain["shared_rate_per_year"]),
        },
        {"source": "World Bank catalogue, 95% upper", "value": share(r_high)},
        {
            "source": "World Bank catalogue, secondary uncertainty (comonotone)",
            "value": share(rain["secondary_uncertainty"]["comonotone_given_event_per_year"]),
        },
        {
            "source": "Organizer illustrative common share (base)",
            "value": float(pool.base_point[1]),
        },
    ]
    # Independence (zero shared rate) is always a candidate, so the evidence range starts at 0.
    evidence_low = (0.0, 0.0)
    evidence_high = (
        max(s["value"] for s in quake_sources[:4]),
        max(s["value"] for s in rain_sources),
    )
    evidence_range = SharedShockRange(
        earthquake_multiplier=(round(evidence_low[0], 6), round(evidence_high[0], 6)),
        rain_share=(round(evidence_low[1], 6), round(evidence_high[1], 6)),
    )
    return {
        "source": SOURCE,
        "pool": pool.name,
        "earthquake": quake,
        "rain": rain,
        "earthquake_tail_dependence": tail_dependence("earthquake", a, b),
        "rain_tail_dependence": tail_dependence("rain", a, b),
        "bond_conditional_trigger": 0.0745,  # mean of the published 0.075 and 0.074
        "earthquake_multiplier_sources": quake_sources,
        "rain_share_sources": rain_sources,
        "evidence_range": evidence_range.to_dict(),
        "stress_range": {"earthquake_multiplier": [0.0, 2.0], "rain_share": [0.0, 0.25]},
        "reading": (
            "Calibrated to the bond's trigger rates, the World Bank catalogue shows much weaker "
            "same-event triggering than the bond documents. The evidence range spans both sources; "
            "the stress range adds the organizer's doubled common-shock scenario."
        ),
    }


def evidence_reserves(evidence: dict, limits=(0.015, 0.005)) -> list:
    """Certified reserve across the evidence range and the stress range."""
    from .bounds import bracket_reserve

    pool = public_two_bond()
    rows = []
    for name in ("evidence_range", "stress_range"):
        spec = evidence[name]
        shock_range = SharedShockRange(
            tuple(spec["earthquake_multiplier"]), tuple(spec["rain_share"])
        )
        for limit in limits:
            result = bracket_reserve(pool, shock_range, limit)
            rows.append(
                {
                    "range": name,
                    "risk_limit": limit,
                    "lower_usd": result.lower_units / 400,
                    "upper_usd": result.upper_units / 400,
                    "base_usd": result.base.lower_units / 400,
                    "evaluations": result.total_evaluations,
                    "status": result.status,
                }
            )
    return rows


def main(argv=None) -> int:
    evidence = public_evidence()
    evidence["reserves"] = evidence_reserves(evidence)
    out = results_dir() / "evidence.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps({"schema": "dlr-evidence-v1", **evidence}, indent=2) + "\n", encoding="utf-8"
    )
    quake, rain = evidence["earthquake"], evidence["rain"]
    print(
        f"Earthquake: {quake['joint_trigger_events']} joint events, "
        f"multiplier {evidence['evidence_range']['earthquake_multiplier']}"
    )
    print(
        f"Rain: {rain['joint_trigger_events']} joint events, "
        f"share {evidence['evidence_range']['rain_share']}"
    )
    for row in evidence["reserves"]:
        print(
            f"{row['range']:>15} {row['risk_limit']:.1%}: "
            f"${row['lower_usd']:,.0f} to ${row['upper_usd']:,.0f} "
            f"({row['evaluations']} evaluations)"
        )
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
