"""Exact monetary primitives. Source-specific cleanup belongs to ingestion."""

from decimal import ROUND_HALF_UP, Decimal, DecimalException, InvalidOperation

CENT = Decimal("0.01")


def parse_decimal(value: object) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ValueError("Expected a finite decimal, not a boolean or missing value")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("Invalid decimal") from exc
    if not amount.is_finite():
        raise ValueError("Expected a finite decimal")
    return amount


def round_cents(value: Decimal) -> Decimal:
    value = parse_decimal(value)
    if value.copy_abs() >= Decimal("1e24"):
        raise ValueError("Monetary magnitude must be less than 1e24")
    try:
        return value.quantize(CENT, rounding=ROUND_HALF_UP)
    except DecimalException as exc:
        raise ValueError("Amount cannot be represented at supported precision") from exc


def money_string(value: Decimal) -> str:
    return format(round_cents(value), ".2f")


def convert_to_usd(value: Decimal, rate: Decimal) -> Decimal:
    rate = parse_decimal(rate)
    if rate <= 0:
        raise ValueError("Exchange rate must be positive")
    try:
        return round_cents(parse_decimal(value) * rate)
    except DecimalException as exc:
        raise ValueError("Converted amount exceeds supported precision") from exc
