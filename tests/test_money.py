from decimal import Decimal

import pytest

from liquidity_reserves.money import decimal_string, format_usd, usd_to_units


@pytest.mark.parametrize(
    "value, units",
    [
        (0, 0),
        (80_000_000, 32_000_000_000),
        ("87,500,000", 35_000_000_000),
        ("$1,234.56", 493_824),
        (Decimal("0.01"), 4),
        (45123456.78, 18_049_382_712),
    ],
)
def test_usd_to_units_is_exact(value, units):
    assert usd_to_units(value) == units


def test_float_representation_noise_is_snapped_to_the_cent():
    noisy = 98_000_000.0 - 61_234_567.89  # 36765432.110000014 in binary floating point
    assert usd_to_units(noisy) == usd_to_units("36765432.11")


@pytest.mark.parametrize("value", [-1, float("nan"), float("inf"), "1.001", "abc", True, None])
def test_invalid_amounts_are_rejected(value):
    with pytest.raises(ValueError):
        usd_to_units(value)


def test_formatting_never_rounds():
    assert format_usd(usd_to_units(87_500_000)) == "$87,500,000"
    assert format_usd(usd_to_units("1234.5")) == "$1,234.50"
    # A quarter step of a cap in odd cents is a quarter-cent amount.
    assert format_usd(usd_to_units("45123456.79") // 4) == "$11,280,864.1975"
    assert decimal_string(usd_to_units("45123456.79") // 4) == "11280864.1975"
