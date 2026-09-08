from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from invoice_agent.models import (
    Critique,
    Decision,
    ErrorCode,
    ErrorInfo,
    InventorySnapshot,
    InvoiceCandidate,
    InvoiceIdentity,
    InvoiceLine,
    InvoiceResult,
    PaymentOutcome,
    PaymentRequest,
    PaymentStatus,
    Proposal,
    ReviewOutcome,
    TraceEvent,
    ValidationReport,
    candidate_digest,
)


def candidate(quantity_raw="2", quantity=Decimal("2")):
    return InvoiceCandidate(
        source_id="s",
        vendor_normalized="Vendor",
        invoice_number_normalized="INV-1",
        invoice_date=date(2026, 1, 1),
        due_date=date(2026, 2, 1),
        total_usd=Decimal("10.00"),
        items=[
            InvoiceLine(
                line_id="1",
                item_name_normalized="WidgetA",
                quantity_raw=quantity_raw,
                quantity=quantity,
            )
        ],
    )


def request(c):
    digest = candidate_digest(c)
    report = ValidationReport(
        candidate_digest=digest,
        aggregate_quantities={"WidgetA": 2},
        stock_snapshot=InventorySnapshot(run_id="r", stock={"WidgetA": 15}),
        complete=True,
    )
    review = ReviewOutcome(
        candidate_digest=digest,
        proposal=Proposal(decision=Decision.APPROVED, reason_summary="Checks pass"),
        critique=Critique(verdict="accept"),
        accepted=True,
    )
    return PaymentRequest(
        run_id="r",
        source_id="s",
        identity=InvoiceIdentity(vendor="Vendor", invoice_number="INV-1"),
        fingerprint="fingerprint",
        amount_usd=Decimal("10.00"),
        aggregate_quantities={"WidgetA": 2},
        candidate=c,
        report=report,
        review=review,
    )


@pytest.mark.parametrize(
    "raw,parsed",
    [("-5", Decimal("-5")), ("2.5", Decimal("2.5")), ("abc", None), (True, None), (None, None)],
)
def test_invalid_tokens_preserved_but_cannot_pay(raw, parsed):
    c = candidate(raw, parsed)
    assert c.items[0].quantity_raw == raw
    with pytest.raises(ValidationError):
        request(c)


def test_payment_and_exact_decimal_roundtrip():
    payment = request(candidate())
    restored = PaymentRequest.model_validate_json(payment.model_dump_json())
    assert restored.amount_usd == Decimal("10.00")
    assert '"amount_usd":"10.00"' in payment.model_dump_json()


def test_digest_includes_terms_and_evidence():
    c = candidate()
    assert candidate_digest(c) != candidate_digest(
        c.model_copy(update={"payment_terms_raw": "Net 30"})
    )
    assert candidate_digest(c) != candidate_digest(
        c.model_copy(update={"field_evidence": {"total": ["ref"]}})
    )


def test_invalid_result_combinations():
    with pytest.raises(ValidationError):
        InvoiceResult(
            run_id="r",
            source_id="s",
            source_path="x",
            status="error",
            decision="rejected",
            error=ErrorInfo(code=ErrorCode.INTERNAL_ERROR, message="failure"),
        )
    with pytest.raises(ValidationError):
        PaymentOutcome(status=PaymentStatus.PAID)
    with pytest.raises(ValidationError):
        InvoiceResult(
            run_id="r",
            source_id="s",
            source_path="x",
            decision="rejected",
            payment=PaymentOutcome(status="paid", payment_id="p"),
        )


def test_trace_redacts_secrets_preserves_ids():
    event = TraceEvent(
        run_id="r",
        source_id="s",
        stage="llm",
        event="response",
        payload={
            "api_key": "sentinel-secret",
            "message": "Bearer abcdefghijklmnop",
            "request_id": "req-1",
        },
    )
    text = event.model_dump_json()
    assert "sentinel-secret" not in text
    assert "abcdefghijklmnop" not in text
    assert event.payload["request_id"] == "req-1"


def test_forged_accepted_critique_with_issues_cannot_pay():
    payment = request(candidate())
    payload = payment.model_dump()
    payload["review"]["critique"]["issues"] = ["Unresolved discrepancy"]
    with pytest.raises(ValidationError):
        PaymentRequest.model_validate(payload)
