"""Exact monetary amounts.

Amounts are stored as integer quarter-cents. Contract amounts are whole cents,
so an earthquake quarter-step payout (cap / 4) is always a whole number of
quarter-cents and payout sums never need rounding.
"""

from __future__ import annotations

import math
from decimal import Decimal, InvalidOperation

UNITS_PER_USD = 400
UNITS_PER_CENT = 4
# Inputs above this size are rejected so sums of many caps stay inside int64.
MAX_AMOUNT_USD = Decimal(10) ** 12
# Binary floats such as 98e6 - 61234567.89 carry representation noise far below
# a cent. Snap that noise; reject genuine sub-cent amounts.
_FLOAT_NOISE_CENTS = Decimal("0.0001")


def usd_to_units(value, name: str = "amount") -> int:
    """Convert a USD amount (int, float, str or Decimal) to integer quarter-cents."""
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a number")
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{name} must be a finite number")
        text = repr(value)
    elif isinstance(value, (int, Decimal)):
        text = str(value)
    elif isinstance(value, str):
        text = value.strip().replace(",", "").replace("$", "")
    else:
        raise ValueError(f"{name} must be a number")
    try:
        amount = Decimal(text)
    except InvalidOperation as error:
        raise ValueError(f"{name} must be a number") from error
    if not amount.is_finite():
        raise ValueError(f"{name} must be a finite number")
    if amount < 0:
        raise ValueError(f"{name} cannot be negative")
    if amount > MAX_AMOUNT_USD:
        raise ValueError(f"{name} exceeds USD {MAX_AMOUNT_USD:,}")
    cents = amount * 100
    whole = cents.to_integral_value()
    if cents != whole and not (
        isinstance(value, float) and abs(cents - whole) <= _FLOAT_NOISE_CENTS
    ):
        raise ValueError(f"{name} must be a whole number of cents")
    return int(whole) * UNITS_PER_CENT


def units_to_decimal(units: int) -> Decimal:
    """Exact USD value of an integer number of quarter-cents."""
    return Decimal(int(units)) / UNITS_PER_USD


def units_to_float(units) -> float:
    return float(units) / UNITS_PER_USD


def format_usd(units: int) -> str:
    """'$87,500,000', '$1,234.50' or '$11,280,864.1975' with no rounding."""
    value = units_to_decimal(units)
    if value == value.to_integral_value():
        return f"${value.quantize(Decimal(1)):,}"
    if value * 100 == (value * 100).to_integral_value():
        return f"${value.quantize(Decimal('0.01')):,}"
    return f"${value.normalize():,f}"


def decimal_string(units: int) -> str:
    """Plain exact decimal string for machine-readable exports."""
    value = units_to_decimal(units)
    if value == value.to_integral_value():
        return str(value.quantize(Decimal(1)))
    return format(value.normalize(), "f")
