"""Payment eligibility construction and side-effect-free banking simulation."""

from decimal import Decimal
from uuid import uuid4

from .identity import payment_fingerprint, payment_identity
from .models import (
    InvoiceCandidate,
    MockPaymentResult,
    PaymentRequest,
    ReviewOutcome,
    ValidationReport,
)


def build_payment_request(
    candidate: InvoiceCandidate, report: ValidationReport, review: ReviewOutcome, run_id: str
) -> PaymentRequest:
    identity, fingerprint = payment_identity(candidate), payment_fingerprint(candidate)
    if identity is None or fingerprint is None or candidate.total_usd is None:
        raise ValueError("Payment requires complete identity and semantic fingerprint")
    return PaymentRequest(
        run_id=run_id,
        source_id=candidate.source_id,
        identity=identity,
        fingerprint=fingerprint,
        amount_usd=candidate.total_usd,
        aggregate_quantities=report.aggregate_quantities,
        candidate=candidate.model_copy(deep=True),
        report=report.model_copy(deep=True),
        review=review.model_copy(deep=True),
    )


def mock_payment(vendor: str, amount_usd: Decimal) -> MockPaymentResult:
    if not vendor.strip() or not amount_usd.is_finite() or amount_usd <= 0:
        return MockPaymentResult(success=False, message="Invalid payment details")
    return MockPaymentResult(
        success=True, payment_id=f"mock-{uuid4().hex}", message="Simulated payment accepted"
    )
