import numpy as np
import pytest

from liquidity_reserves.evidence import (
    evidence_reserves,
    pair_dependence,
    poisson_interval,
    public_evidence,
    trigger_set,
)


def test_trigger_set_takes_the_highest_losses_until_the_rate_is_reached():
    rate = np.full(10, 0.01)
    loss = np.arange(10, dtype=float)
    mask, threshold, attained = trigger_set(rate, loss, 0.03)
    assert threshold == 7.0
    assert mask.sum() == 3 and attained == pytest.approx(0.03)
    _, threshold, attained = trigger_set(rate, loss, 0.031)
    assert threshold == 6.0 and attained == pytest.approx(0.04)


def test_poisson_interval_matches_known_values():
    low, high = poisson_interval(0)
    assert low == 0.0 and high == pytest.approx(3.6889, abs=1e-4)
    low, high = poisson_interval(1)
    assert low == pytest.approx(0.0253, abs=1e-4) and high == pytest.approx(5.5716, abs=1e-4)


@pytest.fixture(scope="module")
def evidence(two_bond):
    return public_evidence(two_bond)


def test_catalogue_triggers_match_the_bond_rates(evidence, two_bond):
    quake = evidence["earthquake"]
    for code, target in zip(two_bond.codes, two_bond.earthquake_marginal, strict=True):
        attained = quake["attained_trigger_rate_per_year"][code]
        # overshoot is at most the event that crosses the target plus its tie group
        assert target <= attained <= target + 3.1e-4


def test_catalogue_shows_weaker_same_event_dependence_than_the_bond(evidence):
    assert evidence["earthquake"]["joint_trigger_events"] == 0
    assert evidence["rain"]["joint_trigger_events"] == 1
    upper = next(s for s in evidence["earthquake_multiplier_sources"] if "95% upper" in s["source"])
    assert upper["value"] < 0.2  # far below the bond documents' 1x
    assert evidence["evidence_range"] == {
        "earthquake_multiplier": [0.0, 1.0],
        "rain_share": [0.0, 0.25],
    }


def test_secondary_uncertainty_brackets_independence_and_comonotonicity(two_bond):
    row = pair_dependence("earthquake", *two_bond.codes, *two_bond.earthquake_marginal)
    sec = row["secondary_uncertainty"]
    assert 0 <= sec["independent_given_event_per_year"] <= sec["comonotone_given_event_per_year"]


def test_evidence_range_needs_no_dependence_uplift_at_one_and_a_half_percent(evidence):
    rows = evidence_reserves(evidence, limits=(0.015,))
    by_range = {r["range"]: r for r in rows}
    assert by_range["evidence_range"]["upper_usd"] == 80_000_000
    assert by_range["stress_range"]["upper_usd"] == 87_500_000
