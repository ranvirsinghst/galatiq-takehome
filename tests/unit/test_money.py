from decimal import Decimal

import pytest

from invoice_agent.money import convert_to_usd, money_string, parse_decimal


@pytest.mark.parametrize("token", [True, False, None, "NaN", "Infinity", "-Infinity", "nonsense"])
def test_invalid_money(token):
    with pytest.raises(ValueError):
        parse_decimal(token)


def test_exact_decimal_and_half_up_conversion():
    assert parse_decimal("0.10") + parse_decimal("0.20") == Decimal("0.30")
    assert money_string(Decimal("1.005")) == "1.01"
    assert convert_to_usd(Decimal("100.05"), Decimal("1.10")) == Decimal("110.06")
