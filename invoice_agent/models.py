"""Shared boundary contracts; invalid source tokens remain explicit data."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class Decision(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"


class ExecutionStatus(StrEnum):
    COMPLETED = "completed"
    ERROR = "error"


class PaymentStatus(StrEnum):
    PAID = "paid"
    ALREADY_PAID = "already_paid"
    NOT_PAID = "not_paid"
    FAILED = "failed"


class Severity(StrEnum):
    BLOCKER = "blocker"
    WARNING = "warning"


class FindingOrigin(StrEnum):
    SOURCE = "source"
    EXTRACTION = "extraction"
    INVENTORY = "inventory"
    ARITHMETIC = "arithmetic"
    IDENTITY = "identity"
    POLICY = "policy"


class ErrorCode(StrEnum):
    SOURCE_PARSE_FAILED = "SOURCE_PARSE_FAILED"
    SOURCE_IO_ERROR = "SOURCE_IO_ERROR"
    LLM_SCHEMA_ERROR = "LLM_SCHEMA_ERROR"
    PROVIDER_TRANSIENT = "PROVIDER_TRANSIENT"
    PROVIDER_PERMANENT = "PROVIDER_PERMANENT"
    CONFIG_ERROR = "CONFIG_ERROR"
    TOOL_PROTOCOL_ERROR = "TOOL_PROTOCOL_ERROR"
    PAYMENT_FAILED = "PAYMENT_FAILED"
    STORAGE_ERROR = "STORAGE_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    EXTRACTION_EXHAUSTED = "EXTRACTION_EXHAUSTED"
    REVIEW_EXHAUSTED = "REVIEW_EXHAUSTED"


class ErrorInfo(Contract):
    code: ErrorCode
    message: str
    retryable: bool = False
    fatal: bool = False


class Evidence(Contract):
    evidence_id: str
    source_id: str
    location: str
    excerpt: str = ""


class Normalization(Contract):
    field: str
    original_value: Any = None
    normalized_value: Any = None
    method: str
    evidence_ids: list[str] = Field(default_factory=list)


class ValidationFinding(Contract):
    code: str
    severity: Severity
    message: str
    field: str | None = None
    line_ids: list[str] = Field(default_factory=list)
    observed: Any = None
    expected: Any = None
    evidence_refs: list[str] = Field(default_factory=list)
    origin: FindingOrigin = FindingOrigin.POLICY


class SourceDocument(Contract):
    source_id: str
    path: str
    format: str
    content_sha256: str
    raw_text: str = ""
    raw_data: Any = None
    evidence: list[Evidence] = Field(default_factory=list)
    reader_warnings: list[ValidationFinding] = Field(default_factory=list)


class InvoiceLine(Contract):
    line_id: str
    description_raw: str | None = None
    item_name_normalized: str | None = None
    quantity_raw: Any = None
    quantity: Decimal | None = None
    unit_price_usd: Decimal | None = None
    line_total_usd: Decimal | None = None
    source_unit_price: Decimal | None = None
    source_line_total: Decimal | None = None
    source_tokens: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)
    normalizations: list[Normalization] = Field(default_factory=list)

    @field_validator("quantity", mode="before")
    @classmethod
    def reject_boolean_quantity(cls, value: Any) -> Any:
        if isinstance(value, bool):
            raise ValueError("Boolean is not a parsed quantity; retain it in quantity_raw")
        return value


class InvoiceCandidate(Contract):
    source_id: str
    invoice_number_raw: str | None = None
    invoice_number_normalized: str | None = None
    revision: str | None = None
    vendor_raw: str | None = None
    vendor_normalized: str | None = None
    invoice_date_raw: str | None = None
    invoice_date: date | None = None
    due_date_raw: str | None = None
    due_date: date | None = None
    payment_terms_raw: str | None = None
    net_days: int | None = None
    source_currency: str | None = None
    fx_rate_to_usd: Decimal | None = None
    items: list[InvoiceLine] = Field(default_factory=list)
    subtotal_usd: Decimal | None = None
    tax_usd: Decimal | None = None
    shipping_usd: Decimal | None = None
    total_usd: Decimal | None = None
    source_amounts: dict[str, Decimal | None] = Field(default_factory=dict)
    source_tokens: dict[str, Any] = Field(default_factory=dict)
    evidence: list[Evidence] = Field(default_factory=list)
    field_evidence: dict[str, list[str]] = Field(default_factory=dict)
    normalizations: list[Normalization] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    findings: list[ValidationFinding] = Field(default_factory=list)


def candidate_digest(candidate: InvoiceCandidate) -> str:
    payload = json.dumps(candidate.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


class InventorySnapshot(Contract):
    run_id: str
    generation: int = Field(default=0, ge=0)
    stock: dict[str, int | None]


class ValidationReport(Contract):
    candidate_digest: str
    findings: list[ValidationFinding] = Field(default_factory=list)
    aggregate_quantities: dict[str, int] = Field(default_factory=dict)
    stock_snapshot: InventorySnapshot
    performed_checks: list[str] = Field(default_factory=list)
    unavailable_checks: list[str] = Field(default_factory=list)
    complete: bool = False
    requires_high_value_review: bool = False

    @property
    def blockers(self) -> list[ValidationFinding]:
        return [finding for finding in self.findings if finding.severity == Severity.BLOCKER]

    @property
    def inventory_generation(self) -> int:
        return self.stock_snapshot.generation


class Proposal(Contract):
    decision: Decision
    reason_summary: str
    finding_codes: list[str] = Field(default_factory=list)
    checks: dict[str, str] = Field(default_factory=dict)
    high_value_review: bool = False
    evidence_refs: list[str] = Field(default_factory=list)


class Critique(Contract):
    verdict: str = Field(pattern="^(accept|revise)$")
    issues: list[str] = Field(default_factory=list)
    required_changes: list[str] = Field(default_factory=list)


class ReviewOutcome(Contract):
    candidate_digest: str
    proposal: Proposal | None = None
    critique: Critique | None = None
    revision_count: int = Field(default=0, ge=0)
    accepted: bool = False
    rejection_reasons: list[str] = Field(default_factory=list)
    error: ErrorInfo | None = None
    semantic_calls: int = 0


class IngestionOutcome(Contract):
    source_id: str
    candidate: InvoiceCandidate | None = None
    findings: list[ValidationFinding] = Field(default_factory=list)
    error: ErrorInfo | None = None
    attempts: int = 0
    rejected: bool = False


class ValidationOutcome(Contract):
    report: ValidationReport | None = None
    error: ErrorInfo | None = None
    tool_rounds: int = 0


class InvoiceIdentity(Contract):
    vendor: str
    invoice_number: str


class PaidRecord(Contract):
    run_id: str
    identity: InvoiceIdentity
    fingerprint: str
    payment_id: str
    amount_usd: Decimal
    source_id: str


class MockPaymentResult(Contract):
    success: bool
    payment_id: str | None = None
    message: str = ""


class PaymentOutcome(Contract):
    status: PaymentStatus = PaymentStatus.NOT_PAID
    payment_id: str | None = None
    amount_usd: Decimal | None = None
    inventory_deltas: dict[str, int] = Field(default_factory=dict)
    findings: list[ValidationFinding] = Field(default_factory=list)
    error: ErrorInfo | None = None

    @model_validator(mode="after")
    def valid_status(self) -> PaymentOutcome:
        if self.status in (PaymentStatus.PAID, PaymentStatus.ALREADY_PAID) and not self.payment_id:
            raise ValueError("Successful payment requires payment_id")
        if self.status == PaymentStatus.FAILED and self.error is None:
            raise ValueError("Failed payment requires structured error")
        return self


class PaymentRequest(Contract):
    run_id: str
    source_id: str
    identity: InvoiceIdentity
    fingerprint: str
    amount_usd: Decimal
    aggregate_quantities: dict[str, int]
    candidate: InvoiceCandidate
    report: ValidationReport
    review: ReviewOutcome

    @model_validator(mode="after")
    def eligible(self) -> PaymentRequest:
        c, r, v = self.candidate, self.report, self.review
        if (
            not self.run_id
            or self.source_id != c.source_id
            or r.stock_snapshot.run_id != self.run_id
        ):
            raise ValueError("Payment run/source binding mismatch")
        if (
            not c.vendor_normalized
            or not c.invoice_number_normalized
            or not c.invoice_date
            or not c.due_date
        ):
            raise ValueError("Payment requires complete invoice identity and dates")
        if self.amount_usd <= 0 or self.amount_usd != c.total_usd:
            raise ValueError("Payment amount must equal positive candidate total")
        if not r.complete or r.blockers or any(f.severity == Severity.BLOCKER for f in c.findings):
            raise ValueError("Payment requires complete blocker-free validation")
        if r.candidate_digest != candidate_digest(c) or v.candidate_digest != r.candidate_digest:
            raise ValueError("Payment evidence digest mismatch")
        if (
            not v.accepted
            or v.error
            or not v.proposal
            or v.proposal.decision != Decision.APPROVED
            or not v.critique
            or v.critique.verdict != "accept"
            or bool(v.critique.issues)
            or bool(v.critique.required_changes)
        ):
            raise ValueError("Payment requires accepted approval and critique")
        issues = review_eligibility_issues(c, r, v.proposal)
        if issues:
            raise ValueError("; ".join(issues))
        quantities: dict[str, int] = {}
        for line in c.items:
            q = line.quantity
            if (
                isinstance(line.quantity_raw, bool)
                or q is None
                or q <= 0
                or q != q.to_integral_value()
                or not line.item_name_normalized
            ):
                raise ValueError("Payment requires positive integral quantities and named items")
            quantities[line.item_name_normalized] = quantities.get(
                line.item_name_normalized, 0
            ) + int(q)
        if (
            not quantities
            or quantities != self.aggregate_quantities
            or quantities != r.aggregate_quantities
        ):
            raise ValueError("Payment aggregate quantities mismatch")
        return self


class LLMToolCall(Contract):
    call_id: str
    name: str
    arguments: dict[str, Any]


class LLMRequest(Contract):
    phase: str
    messages: list[dict[str, Any]]
    output_schema: dict[str, Any] | None = None
    tools: list[dict[str, Any]] = Field(default_factory=list)
    max_output_tokens: int = Field(default=4096, gt=0)


class LLMResponse(Contract):
    content: dict[str, Any] | None = None
    tool_calls: list[LLMToolCall] = Field(default_factory=list)
    finish_reason: str = "stop"
    usage: dict[str, int] = Field(default_factory=dict)
    provider_request_id: str | None = None
    transport_retries: int = 0


_SECRET = re.compile(r"(?i)(?:sk-[a-z0-9_-]{8,}|(?:bearer\s+)[a-z0-9_.-]+)")


def redact(value: Any) -> Any:
    if isinstance(value, str):
        return _SECRET.sub("[REDACTED]", value)[:2000]
    if isinstance(value, dict):
        return {
            str(k): "[REDACTED]"
            if any(s in str(k).lower() for s in ("key", "secret", "authorization", "token"))
            else redact(v)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value[:100]]
    return value


class TraceEvent(Contract):
    schema_version: int = 1
    run_id: str
    source_id: str | None = None
    sequence: int = Field(default=0, ge=0)
    stage: str
    event: str
    elapsed_ms: float | None = None
    severity: str = "info"
    payload: dict[str, Any] = Field(default_factory=dict)
    error_code: ErrorCode | None = None

    @field_validator("payload", mode="before")
    @classmethod
    def safe_payload(cls, value: Any) -> Any:
        return redact(value)


class InvoiceResult(Contract):
    candidate: InvoiceCandidate | None = None
    validation: ValidationReport | None = None
    review: ReviewOutcome | None = None
    source_sha256: str | None = None
    run_id: str
    source_id: str
    source_path: str
    processing_index: int | None = None
    identity: InvoiceIdentity | None = None
    invoice_date: date | None = None
    total_usd: Decimal | None = None
    source_currency: str | None = None
    fx_rate_to_usd: Decimal | None = None
    status: ExecutionStatus = ExecutionStatus.COMPLETED
    decision: Decision | None = None
    reasons: list[str] = Field(default_factory=list)
    findings: list[ValidationFinding] = Field(default_factory=list)
    payment: PaymentOutcome = Field(default_factory=PaymentOutcome)
    attempts: dict[str, int] = Field(default_factory=dict)
    error: ErrorInfo | None = None
    trace: list[TraceEvent] | None = None

    @model_validator(mode="after")
    def coherent_status(self) -> InvoiceResult:
        if self.status == ExecutionStatus.ERROR:
            if self.error is None or self.decision is not None:
                raise ValueError(
                    "Operational error requires structured error and no business decision"
                )
        elif self.error is not None or self.decision is None:
            raise ValueError("Completed invoice requires a business decision and no error")
        if self.payment.status in (PaymentStatus.PAID, PaymentStatus.ALREADY_PAID) and (
            self.status != ExecutionStatus.COMPLETED or self.decision != Decision.APPROVED
        ):
            raise ValueError("Paid invoice requires approved/completed status")
        return self


class RunSummary(Contract):
    run_id: str
    error: ErrorInfo | None = None
    discovered: int = 0
    completed: int = 0
    approved: int = 0
    rejected: int = 0
    duplicate_skips: int = 0
    operational_errors: int = 0
    new_payments: int = 0
    total_paid_usd: Decimal = Decimal("0.00")
    final_inventory: dict[str, int | None] = Field(default_factory=dict)
    skipped: list[str] = Field(default_factory=list)


class RunResult(Contract):
    results: list[InvoiceResult]
    summary: RunSummary
    trace: list[TraceEvent] = Field(default_factory=list)


def review_eligibility_issues(
    candidate: InvoiceCandidate, report: ValidationReport, proposal: Proposal
) -> list[str]:
    """Mechanical checklist shared by VP reflection and payment authority gate."""
    issues: list[str] = []
    known_codes = {f.code for f in report.findings}
    cited_codes = set(proposal.finding_codes)
    if cited_codes - known_codes:
        issues.append("Proposal references unknown finding codes")
    if report.blockers and proposal.decision == Decision.APPROVED:
        issues.append("Approval cannot override validation blockers")
    warnings = {f.code for f in report.findings if f.severity == Severity.WARNING}
    if warnings - cited_codes:
        issues.append("Proposal must acknowledge all warning codes")
    evidence_ids = {e.evidence_id for e in candidate.evidence}
    evidence_ids.update(e for refs in candidate.field_evidence.values() for e in refs)
    evidence_ids.update(e for line in candidate.items for e in line.evidence_refs)
    if set(proposal.evidence_refs) - evidence_ids:
        issues.append("Proposal references unknown evidence")
    if report.requires_high_value_review:
        required = {"arithmetic", "aggregate_stock", "data_completeness", "suspicious_signals"}
        if not proposal.high_value_review or any(
            not proposal.checks.get(key, "").strip() for key in required
        ):
            issues.append(
                "High-value review requires arithmetic, aggregate_stock, data_completeness, suspicious_signals"
            )
    if report.unavailable_checks and not proposal.checks.get("unavailable_checks", "").strip():
        issues.append("Proposal must acknowledge unavailable arithmetic checks")
    return issues
