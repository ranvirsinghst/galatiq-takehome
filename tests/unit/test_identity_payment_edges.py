from decimal import Decimal

import pytest

from invoice_agent.identity import payment_fingerprint, payment_identity
from invoice_agent.models import ReviewOutcome
from invoice_agent.payment import build_payment_request, mock_payment
from tests.unit.test_validation import candidate, report


@pytest.mark.parametrize(
    "changes",
    [
        {"vendor_normalized": None},
        {"invoice_number_normalized": None},
        {"vendor_normalized": "   "},
        {"due_date": None},
        {"items": []},
    ],
)
def test_incomplete_invoice_cannot_be_fingerprinted_or_paid(changes):
    c = candidate(**changes)
    assert payment_fingerprint(c) is None
    with pytest.raises(ValueError, match="complete identity"):
        build_payment_request(c, report(c), ReviewOutcome(candidate_digest="x"), "run")


@pytest.mark.parametrize(
    "changes",
    [
        {"item_name_normalized": None},
        {"quantity": None},
        {"quantity_raw": True},
    ],
)
def test_incomplete_line_cannot_be_equated_to_a_paid_invoice(changes):
    c = candidate(items=[candidate().items[0].model_copy(update=changes)])
    assert payment_fingerprint(c) is None


def test_explicit_and_derived_subtotals_are_equivalent_without_fabricating_unknowns():
    c = candidate()
    derived = c.model_copy(update={"source_amounts": {"total": Decimal(20)}})
    assert payment_fingerprint(c) == payment_fingerprint(derived)
    unknown = derived.model_copy(
        update={
            "items": [
                derived.items[0].model_copy(
                    update={
                        "source_unit_price": None,
                        "source_line_total": None,
                    }
                )
            ]
        }
    )
    assert payment_fingerprint(unknown) != payment_fingerprint(derived)


def test_custom_invoice_identifiers_remain_distinct_and_vendor_whitespace_normalizes():
    c = candidate(vendor_normalized="  Acme   Corp ", invoice_number_normalized="PO-A")
    identity = payment_identity(c)
    assert identity is not None
    assert identity.vendor == "acme corp"
    assert identity.invoice_number == "PO-A"
    assert identity != payment_identity(c.model_copy(update={"invoice_number_normalized": "PO-B"}))


@pytest.mark.parametrize(
    "vendor,amount",
    [
        (" ", Decimal(10)),
        ("Vendor", Decimal(0)),
        ("Vendor", Decimal(-1)),
        ("Vendor", Decimal("NaN")),
        ("Vendor", Decimal("Infinity")),
    ],
)
def test_mock_payment_declines_invalid_payable_inputs(vendor, amount):
    result = mock_payment(vendor, amount)
    assert not result.success
    assert result.payment_id is None
