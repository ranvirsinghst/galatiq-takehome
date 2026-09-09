from datetime import date
from decimal import Decimal

import pytest

from invoice_agent.config import Policy
from invoice_agent.models import InventorySnapshot, InvoiceCandidate, InvoiceLine
from invoice_agent.validation import validate


def candidate(quantity=2, **changes):
    line = InvoiceLine(
        line_id="1",
        item_name_normalized="WidgetA",
        quantity_raw=quantity,
        quantity=None if isinstance(quantity, bool) or quantity is None else Decimal(str(quantity)),
        source_unit_price=Decimal("10"),
        unit_price_usd=Decimal("10"),
        source_line_total=Decimal("20"),
    )
    data = dict(
        source_id="s1",
        vendor_normalized="Vendor",
        invoice_number_normalized="INV-1",
        invoice_date=date(2026, 1, 1),
        due_date=date(2026, 1, 31),
        source_currency="USD",
        fx_rate_to_usd=Decimal(1),
        items=[line],
        total_usd=Decimal("20"),
        source_amounts={"total": Decimal("20"), "subtotal": Decimal("20")},
    )
    data.update(changes)
    return InvoiceCandidate(**data)


def report(c, stock=None):
    return validate(
        c,
        InventorySnapshot(
            run_id="run", stock=stock or {"WidgetA": 15, "WidgetB": 10, "GadgetX": 5, "FakeItem": 0}
        ),
        Policy(),
    )


def codes(r):
    return {f.code for f in r.findings}


def test_clean_and_threshold():
    assert not report(candidate()).blockers
    assert not report(candidate(total_usd=Decimal("10000"))).requires_high_value_review
    assert report(candidate(total_usd=Decimal("10000.01"))).requires_high_value_review


@pytest.mark.parametrize("q", [0, -1, 2.5, True, None])
def test_invalid_quantity(q):
    assert "INVALID_QUANTITY" in codes(report(candidate(q)))


def test_aggregate_and_negative_do_not_cancel():
    first = candidate(15).items[0]
    c = candidate(
        items=[
            first,
            first.model_copy(update={"line_id": "2", "quantity": Decimal(5)}),
            first.model_copy(update={"line_id": "3", "quantity": Decimal(-9)}),
        ]
    )
    r = report(c)
    assert r.aggregate_quantities == {"WidgetA": 20}
    assert {"INVALID_QUANTITY", "INSUFFICIENT_STOCK"} <= codes(r)


@pytest.mark.parametrize(
    "item,stock,expected",
    [
        ("WidgetC", None, "UNKNOWN_ITEM"),
        ("FakeItem", 0, "OUT_OF_STOCK"),
        ("WidgetA", 1, "INSUFFICIENT_STOCK"),
    ],
)
def test_inventory_cases(item, stock, expected):
    c = candidate()
    c = c.model_copy(
        update={"items": [c.items[0].model_copy(update={"item_name_normalized": item})]}
    )
    assert expected in codes(report(c, {item: stock}))


@pytest.mark.parametrize("delta,blocked", [("0.01", False), ("0.02", True)])
def test_arithmetic_cent_tolerance(delta, blocked):
    c = candidate(source_amounts={"subtotal": Decimal(20), "total": Decimal(20) + Decimal(delta)})
    assert ("TOTAL_MISMATCH" in codes(report(c))) == blocked


def test_terms_distinct_from_quantity():
    c = candidate(20, invoice_date=date(2026, 1, 30), due_date=date(2026, 1, 30), net_days=30)
    c = c.model_copy(
        update={"items": [c.items[0].model_copy(update={"item_name_normalized": "GadgetX"})]}
    )
    r = report(c)
    assert {"TERMS_DATE_MISMATCH", "INSUFFICIENT_STOCK"} <= codes(r)
    assert next(f for f in r.findings if f.code == "TERMS_DATE_MISMATCH").expected == "2026-03-01"


def test_leap_year_terms():
    assert "TERMS_DATE_MISMATCH" not in codes(
        report(candidate(invoice_date=date(2024, 2, 1), due_date=date(2024, 3, 2), net_days=30))
    )


def test_1013_fifty_dollar_discrepancy():
    c = candidate(
        items=[
            candidate()
            .items[0]
            .model_copy(update={"source_line_total": None, "source_unit_price": None})
        ],
        source_amounts={
            "subtotal": Decimal("21040"),
            "tax_amount": Decimal("1472.80"),
            "total": Decimal("22562.80"),
        },
    )
    r = report(c)
    assert "TOTAL_MISMATCH" in codes(r)
    assert "line:1" in r.unavailable_checks


@pytest.mark.parametrize("field", ["subtotal", "tax_amount", "shipping", "total"])
def test_malformed_optional_money(field):
    c = candidate(source_tokens={field: "broken"}, source_amounts={field: None})
    assert "INVALID_AMOUNT" in codes(report(c))


@pytest.mark.parametrize(
    "tax,blocked", [("1", False), ("1.01", False), ("1.02", True), ("20", True)]
)
def test_stated_tax_rate_checked_with_source_currency_tolerance(tax, blocked):
    c = candidate(
        total_usd=Decimal(20) + Decimal(tax),
        source_amounts={
            "subtotal": Decimal(20),
            "tax_rate": Decimal(".05"),
            "tax_amount": Decimal(tax),
            "total": Decimal(20) + Decimal(tax),
        },
    )
    r = report(c)
    assert "subtotal_tax_rate" in r.performed_checks
    discrepancies = [f for f in r.findings if f.field == "tax_amount"]
    assert bool(discrepancies) == blocked
    if blocked:
        assert discrepancies[0].expected == "1.00"
        assert discrepancies[0].observed == tax
        assert discrepancies[0].origin == "arithmetic"


@pytest.mark.parametrize("raw,parsed", [("broken", None), ("-0.05", Decimal("-.05")), (True, None)])
def test_invalid_tax_rate_is_a_source_blocker(raw, parsed):
    from invoice_agent.validation import validate_source

    c = candidate(
        source_tokens={"tax_rate": raw},
        source_amounts={
            "subtotal": Decimal(20),
            "total": Decimal(20),
            "tax_rate": parsed,
        },
    )
    assert any(
        f.code == "INVALID_AMOUNT" and f.field == "tax_rate" for f in validate_source(c, Policy())
    )


def test_source_validation_does_not_assume_inventory_and_preserves_warnings():
    from invoice_agent.validation import validate_source

    c = candidate(assumptions=["Missing currency assumed USD"])
    findings = validate_source(c, Policy())
    assert [f.code for f in findings] == ["SOURCE_ASSUMPTION"]
    assert report(c, {"WidgetA": 0}).blockers[0].code == "OUT_OF_STOCK"


def test_stated_tax_rate_without_explicit_subtotal_is_unavailable():
    c = candidate(source_amounts={"total": Decimal(20), "tax_rate": Decimal(".05")})
    r = report(c)
    assert "subtotal_tax_rate" in r.unavailable_checks
    assert "subtotal_tax_rate" not in r.performed_checks


def test_overflowing_net_terms_is_source_blocker():
    from invoice_agent.validation import validate_source

    assert any(
        f.code == "INVALID_PAYMENT_TERMS"
        for f in validate_source(candidate(net_days=10**20), Policy())
    )
