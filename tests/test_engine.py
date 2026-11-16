import numpy as np
import pytest

from liquidity_reserves.bounds import GUARD, bracket_reserve, grid_reserve
from liquidity_reserves.calibration import earthquake_jump_rates
from liquidity_reserves.contraction import (
    SparseRegister,
    choose_registers,
    distribution,
    local_tensors,
)
from liquidity_reserves.curve import Curve
from liquidity_reserves.exact import ctmc_curve, ctmc_distribution
from liquidity_reserves.examples import DEFAULT_RANGE, load_terms, synthetic_chain
from liquidity_reserves.money import usd_to_units
from liquidity_reserves.poisson import poisson_buckets, severity_powers
from liquidity_reserves.portfolio import Country, InputError, RegionalPool, SharedShockRange

M = 1_000_000


def make_country(code, quake=0.04, rain=0.02, cap=70 * M, rain_cap=10 * M, severity=None):
    return Country(
        code=code,
        name=code,
        earthquake_rate=quake,
        severity=severity or (0.4, 0.2, 0.2, 0.2),
        rain_rate=rain,
        earthquake_cap_units=usd_to_units(cap),
        rain_cap_units=usd_to_units(rain_cap),
    )


def make_pool(countries, shared=0.003, years=3, base=(1.0, 0.25)):
    return RegionalPool(
        name="test",
        countries=tuple(countries),
        term_years=years,
        base_shared_earthquake=tuple([shared] * (len(countries) - 1)),
        base_point=base,
    )


# --- building blocks -------------------------------------------------------


def test_poisson_buckets_sum_to_one_and_handle_zero():
    buckets = poisson_buckets(np.array([0.0, 1e-9, 0.3, 7.0]))
    assert np.allclose(buckets.sum(axis=1), 1, atol=1e-15)
    assert buckets[0].tolist() == [1.0, 0.0, 0.0, 0.0, 0.0]
    with pytest.raises(ValueError):
        poisson_buckets(-0.1)
    with pytest.raises(ValueError):
        poisson_buckets(float("nan"))


def test_four_or_more_events_always_exhaust_the_cap():
    powers = severity_powers((0.7, 0.1, 0.1, 0.1))
    assert np.allclose(powers.sum(axis=1), 1)
    assert powers[4].tolist() == [0, 0, 0, 0, 1]
    assert powers[0].tolist() == [1, 0, 0, 0, 0]


def test_calibration_matches_the_organizer(two_bond):
    from qjlb.bond import earthquake_jump_rates as reference

    terms = load_terms()
    for code in ("KGZ", "TJK"):
        tails = terms["annual_earthquake_exceedance_probabilities"][code]
        assert np.array_equal(earthquake_jump_rates(tails), reference(tails))


# --- inputs ----------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"quake": -0.1}, "cannot be negative"),
        ({"quake": float("nan")}, "finite"),
        ({"rain": float("inf")}, "finite"),
        ({"severity": (0.5, 0.5, 0.5, 0.0)}, "sum to 1"),
        ({"severity": (1.2, -0.2, 0.0, 0.0)}, "nonnegative"),
    ],
)
def test_invalid_country_inputs(kwargs, message):
    with pytest.raises(InputError, match=message):
        make_country("AAA", **kwargs)


def test_sub_cent_caps_are_rejected():
    with pytest.raises(InputError, match="whole number of cents"):
        Country("A", "A", 0.01, (1, 0, 0, 0), 0.01, 3, 0)


def test_shared_rate_above_a_marginal_is_reported_by_country():
    pool = make_pool([make_country("AAA", quake=0.004), make_country("BBB")], shared=0.003)
    problems = pool.infeasibilities((2.0, 0.0))
    assert len(problems) == 1 and "AAA" in problems[0]
    with pytest.raises(InputError, match="AAA"):
        pool.rates((2.0, 0.0))
    with pytest.raises(InputError):
        bracket_reserve(pool, SharedShockRange((0.0, 2.0), (0.0, 0.25)), 0.015)


def test_interior_country_counts_both_neighbours():
    # 0.004 / 2 per pair is feasible for the end countries but not for the middle one.
    pool = make_pool([make_country(c, quake=0.006) for c in "ABC"], shared=0.002)
    problems = pool.infeasibilities((2.0, 0.0))
    assert len(problems) == 1 and "next to B" in problems[0]


@pytest.mark.parametrize(
    "limits",
    [(0.0, 2.0, -0.1, 0.25), (1.0, 0.5, 0.0, 0.25), (0.0, float("nan"), 0.0, 0.25)],
)
def test_invalid_ranges(limits):
    with pytest.raises(InputError):
        SharedShockRange(limits[:2], limits[2:])


@pytest.mark.parametrize("limit", [0, 1, 1.5, -0.01, float("nan"), True])
def test_invalid_risk_limits(two_bond, limit):
    with pytest.raises(InputError):
        bracket_reserve(two_bond, DEFAULT_RANGE, limit)


# --- distributions -----------------------------------------------------------


def test_marginal_rates_are_preserved_as_shared_rates_vary(chain5):
    rates_low = chain5.rates((0.0, 0.0))
    rates_high = chain5.rates((2.0, 0.25))
    for rates in (rates_low, rates_high):
        incident_quake = np.r_[rates.earthquake_shared, 0] + np.r_[0, rates.earthquake_shared]
        incident_rain = np.r_[rates.rain_shared, 0] + np.r_[0, rates.rain_shared]
        assert np.allclose(rates.earthquake_private + incident_quake, chain5.earthquake_marginal)
        assert np.allclose(rates.rain_private + incident_rain, chain5.rain_marginal)


def test_zero_shared_rates_give_independent_countries():
    pool = make_pool([make_country("A"), make_country("B", quake=0.03, cap=50 * M)])
    registers = choose_registers(pool)
    curve = distribution(pool, pool.rates((0.0, 0.0)), registers.lower)
    tensors, _ = local_tensors(pool, pool.rates((0.0, 0.0)))
    first = tensors[0][0, 0]
    second = tensors[1][0, 0]
    pay = pool.payout_table()
    expected = {}
    for o1, p1 in enumerate(first):
        for o2, p2 in enumerate(second):
            amount = int(pay[0, o1] + pay[1, o2])
            expected[amount] = expected.get(amount, 0.0) + p1 * p2
    for amount, probability in expected.items():
        j = int(np.searchsorted(curve.support_units, amount))
        assert curve.probabilities[j] == pytest.approx(probability, abs=1e-16)
    assert curve.total_mass == pytest.approx(1.0, abs=1e-14)


def test_tensor_contraction_matches_state_space_solver():
    rng = np.random.default_rng(7)
    countries = [
        make_country(c, quake=q, rain=r, severity=tuple(rng.dirichlet(np.ones(4))))
        for c, q, r in (("A", 0.05, 0.03), ("B", 0.04, 0.02), ("C", 0.06, 0.025))
    ]
    pool = make_pool(countries, shared=0.004)
    registers = choose_registers(pool)
    for point in ((0.0, 0.0), (1.3, 0.1), (2.0, 0.25)):
        rates = pool.rates(point)
        tensor = distribution(pool, rates, registers.lower)
        exact = ctmc_curve(pool, rates, registers.lower.support_units)
        assert np.max(np.abs(tensor.tail - exact.tail)) < 1e-14


def test_public_curve_matches_organizer_exact_reference(two_bond):
    from qjlb.bond import BondModel

    from liquidity_reserves.paths import benchmark_dir

    model = BondModel.load(benchmark_dir() / "scenarios" / "adb-base.json")
    term = model.term_distribution()
    loss = model.principal_loss(model.states).sum(axis=1)
    registers = choose_registers(two_bond)
    assert registers.exact and registers.lower.step_units == usd_to_units(2_500_000)
    curve = distribution(two_bond, two_bond.rates(two_bond.base_point), registers.lower)
    reference = np.array([term[loss > x / 400].sum() for x in curve.support_units])
    assert np.max(np.abs(reference - curve.tail)) < 1e-15


def test_payouts_never_exceed_caps_and_saturate_at_high_rates():
    pool = make_pool(
        [make_country("A", quake=40.0, rain=40.0), make_country("B", quake=40.0, rain=40.0)],
        shared=0.0,
    )
    registers = choose_registers(pool)
    curve = distribution(pool, pool.rates((0.0, 0.0)), registers.lower)
    total_caps = sum(c.principal_units for c in pool.countries)
    assert curve.support_units[-1] == total_caps
    assert curve.probabilities[-1] == pytest.approx(1.0, abs=1e-12)
    assert curve.exceedance(total_caps) == 0.0


def test_payout_exactly_equal_to_the_reserve_is_covered():
    # One country, precipitation only: payout is 0 or exactly USD 10m.
    country = make_country("A", quake=0.0, rain=0.05, cap=0, rain_cap=10 * M)
    pool = make_pool([country], years=1)
    curve = distribution(pool, pool.rates((0.0, 0.0)), choose_registers(pool).lower)
    p_rain = -np.expm1(-0.05)
    ten = usd_to_units(10 * M)
    assert curve.exceedance(ten) == 0.0  # L = R is not a shortfall
    assert curve.exceedance(ten - 4) == pytest.approx(p_rain)  # one cent less falls short
    assert curve.reserve_units(0.01) == ten
    assert curve.reserve_units(0.06) == 0
    assert curve.expected_excess(ten) == 0.0
    assert curve.expected_excess(0) == pytest.approx(10 * M * p_rain)


def test_odd_cent_caps_use_an_exact_sparse_register():
    countries = [
        make_country("A", cap=45_123_456.79, rain_cap=9_876_543.21),
        make_country("B", cap=61_234_567.89, rain_cap=7_654_321.01),
    ]
    pool = make_pool(countries)
    registers = choose_registers(pool)
    assert registers.exact and isinstance(registers.lower, SparseRegister)
    rates = pool.rates((1.0, 0.25))
    tensor = distribution(pool, rates, registers.lower)
    amounts, probabilities = ctmc_distribution(pool, rates)
    assert np.array_equal(amounts, tensor.support_units)
    assert np.max(np.abs(probabilities - tensor.probabilities)) < 1e-15


def test_rounded_register_brackets_the_exact_reserve():
    rng = np.random.default_rng(11)
    countries = [
        make_country(
            f"C{i}",
            cap=round(float(rng.uniform(40, 70)) * M, 2),
            rain_cap=round(float(rng.uniform(5, 10)) * M, 2),
        )
        for i in range(4)
    ]
    pool = make_pool(countries)
    exact = choose_registers(pool)
    assert exact.exact
    rounded = choose_registers(pool, rounding_tick_units=usd_to_units(1 * M))
    assert rounded.mode == "rounded" and rounded.rounding_bound_units > 0
    shock = SharedShockRange((0.0, 2.0), (0.0, 0.25))
    for limit in (0.05, 0.015):
        truth = bracket_reserve(pool, shock, limit, registers=exact)
        approx = bracket_reserve(pool, shock, limit, registers=rounded)
        assert approx.lower_units <= truth.lower_units <= truth.upper_units <= approx.upper_units
        assert approx.base.lower_units <= truth.base.lower_units <= approx.base_upper_units


# --- reserve bounds ----------------------------------------------------------


def test_two_bond_reserves_and_evaluation_counts(two_bond):
    loose = bracket_reserve(two_bond, DEFAULT_RANGE, 0.015)
    assert loose.base.lower_units == usd_to_units(80 * M)
    assert loose.lower_units == loose.upper_units == usd_to_units(87.5 * M)
    assert loose.total_evaluations == 7
    strict = bracket_reserve(two_bond, DEFAULT_RANGE, 0.005)
    assert strict.lower_units == strict.upper_units == usd_to_units(105 * M)
    assert strict.total_evaluations == 39
    grid = grid_reserve(two_bond, DEFAULT_RANGE, 0.005, subdivisions=16)
    assert grid.total_evaluations == 517


def test_witness_is_feasible_and_proves_the_lower_bound(two_bond):
    result = bracket_reserve(two_bond, DEFAULT_RANGE, 0.015)
    assert result.shock_range.contains(result.witness.point)
    assert not two_bond.infeasibilities(result.witness.point)
    smaller, exceedance = result.shortfall_evidence()
    assert smaller < result.lower_units
    assert exceedance > 0.015 + GUARD
    # Recompute the witness independently.
    rates = two_bond.rates(result.witness.point)
    exact = ctmc_curve(two_bond, rates, result.witness.curve.support_units)
    assert exact.exceedance(smaller) == pytest.approx(exceedance, abs=1e-14)


def test_upper_bound_is_sufficient_in_every_cell(two_bond):
    result = bracket_reserve(two_bond, DEFAULT_RANGE, 0.005)
    assert all(c.curve.exceedance(result.upper_units) <= 0.005 for c in result.leaves)
    # Cells tile the range exactly.
    area = sum(np.prod(c.high - c.low) for c in result.leaves)
    assert area == pytest.approx(np.prod(DEFAULT_RANGE.high - DEFAULT_RANGE.low))
    rng = np.random.default_rng(5)
    for _ in range(10):
        point = rng.uniform(DEFAULT_RANGE.low, DEFAULT_RANGE.high)
        curve = distribution(two_bond, two_bond.rates(point), result.registers.lower)
        assert curve.exceedance(result.upper_units) <= 0.005


def test_budget_exhaustion_returns_the_remaining_bracket():
    pool = synthetic_chain(10)
    result = bracket_reserve(pool, DEFAULT_RANGE, 0.015, max_evaluations=15)
    assert result.status == "budget"
    assert result.total_evaluations <= 15
    assert result.lower_units < result.upper_units
    full = bracket_reserve(pool, DEFAULT_RANGE, 0.015, gap_usd=250_000)
    assert result.lower_units <= full.lower_units <= full.upper_units <= result.upper_units


def test_gap_tolerance_stops_early(chain5):
    exact = bracket_reserve(chain5, DEFAULT_RANGE, 0.015)
    loose = bracket_reserve(chain5, DEFAULT_RANGE, 0.015, gap_usd=1_000_000)
    assert exact.resolved and exact.total_evaluations == 567
    assert loose.total_evaluations < exact.total_evaluations
    assert loose.upper_units - loose.lower_units <= usd_to_units(1_000_000)
    assert loose.lower_units <= exact.lower_units <= loose.upper_units


def test_single_point_range_resolves_to_the_point_reserve(two_bond):
    point = SharedShockRange((1.0, 1.0), (0.25, 0.25))
    result = bracket_reserve(two_bond, point, 0.015)
    assert result.resolved
    assert result.upper_units == result.base.lower_units == usd_to_units(80 * M)


def test_base_outside_range_is_not_a_witness(two_bond):
    shock = SharedShockRange((0.0, 0.5), (0.0, 0.1))
    result = bracket_reserve(two_bond, shock, 0.015)
    assert result.witness.role != "base"
    assert result.shock_range.contains(result.witness.point)


def test_zero_reserve_when_limit_is_loose(two_bond):
    result = bracket_reserve(two_bond, DEFAULT_RANGE, 0.5)
    assert result.lower_units == result.upper_units == 0
    assert result.shortfall_evidence() is None


def test_curve_validation():
    with pytest.raises(ValueError):
        Curve(np.array([0, 0]), np.array([0.5, 0.5]))
    curve = Curve(np.array([0, 4, 8]), np.array([0.5, 0.25, 0.25]))
    assert curve.tail.tolist() == [0.5, 0.25, 0.0]
    assert curve.exceedance(5) == 0.25
    with pytest.raises(ValueError):
        curve.exceedance(-1)
