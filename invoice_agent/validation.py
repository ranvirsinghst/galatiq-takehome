"""Pure source/arithmetic and stock policy. No I/O or model judgment."""

from datetime import timedelta
from decimal import Decimal

from .config import Policy
from .models import (
    FindingOrigin,
    InventorySnapshot,
    InvoiceCandidate,
    Severity,
    ValidationFinding,
    ValidationReport,
    candidate_digest,
)
from .money import convert_to_usd


def _source_validation(
    candidate: InvoiceCandidate, policy: Policy
) -> tuple[list[ValidationFinding], list[str], list[str], dict[str, int]]:
    findings = list(candidate.findings)
    performed, unavailable = [], []
    demand: dict[str, int] = {}

    def add(
        code,
        message,
        *,
        field=None,
        severity="blocker",
        origin="policy",
        observed=None,
        expected=None,
        line_ids=None,
    ):
        findings.append(
            ValidationFinding(
                code=code,
                message=message,
                severity=severity,
                origin=origin,
                field=field,
                observed=observed,
                expected=expected,
                line_ids=line_ids or [],
            )
        )

    for name, required_value in [
        ("vendor", candidate.vendor_normalized),
        ("invoice_number", candidate.invoice_number_normalized),
        ("invoice_date", candidate.invoice_date),
        ("due_date", candidate.due_date),
        ("items", candidate.items),
    ]:
        if not required_value:
            add("MISSING_REQUIRED_FIELD", f"Missing or invalid {name}", field=name)
    if candidate.source_currency not in policy.fx_rates:
        add("UNSUPPORTED_CURRENCY", "Unsupported source currency", field="source_currency")
    if candidate.total_usd is None or candidate.total_usd <= 0:
        add("INVALID_AMOUNT", "Payable USD total must be positive", field="total")
    rate = policy.fx_rates.get(candidate.source_currency or "")
    source_total = candidate.source_amounts.get("total")
    if source_total is None or source_total <= 0:
        add("INVALID_AMOUNT", "Source payable total must be positive", field="total")
    if rate is not None and candidate.fx_rate_to_usd != rate:
        add(
            "INVALID_FX_RATE", "Applied rate differs from fixed policy rate", field="fx_rate_to_usd"
        )
    if rate is not None and source_total is not None:
        if convert_to_usd(source_total, rate) != candidate.total_usd:
            add(
                "USD_CONVERSION_MISMATCH",
                "USD payable total differs from direct source conversion",
                field="total_usd",
            )
    for assumption in candidate.assumptions:
        add("SOURCE_ASSUMPTION", assumption, severity="warning", origin="source")
    for line in candidate.items:
        q = line.quantity
        valid_q = (
            not isinstance(line.quantity_raw, bool)
            and q is not None
            and q.is_finite()
            and q > 0
            and q == q.to_integral_value()
        )
        if not valid_q:
            add(
                "INVALID_QUANTITY",
                "Quantity must be a positive integer",
                field="quantity",
                line_ids=[line.line_id],
                observed=line.quantity_raw,
            )
        item = line.item_name_normalized
        if not item:
            add("MISSING_REQUIRED_FIELD", "Missing item name", line_ids=[line.line_id])
        elif valid_q and q is not None:
            demand[item] = demand.get(item, 0) + int(q)
        for key, value in [
            ("unit_price", line.source_unit_price),
            ("amount", line.source_line_total),
        ]:
            if (line.source_tokens.get(key) is not None and value is None) or (
                value is not None and (not value.is_finite() or value < 0)
            ):
                add("INVALID_AMOUNT", f"Invalid line {key}", field=key, line_ids=[line.line_id])
        if (
            valid_q
            and q is not None
            and line.source_unit_price is not None
            and line.source_line_total is not None
        ):
            expected = q * line.source_unit_price
            performed.append(f"line:{line.line_id}")
            if abs(expected - line.source_line_total) > policy.arithmetic_tolerance:
                add(
                    "TOTAL_MISMATCH",
                    "Line amount differs from quantity times price",
                    origin="arithmetic",
                    line_ids=[line.line_id],
                    observed=str(line.source_line_total),
                    expected=str(expected),
                )
        else:
            unavailable.append(f"line:{line.line_id}")
    amounts = candidate.source_amounts
    for key in ("subtotal", "tax_amount", "shipping", "total", "tax_rate"):
        value = amounts.get(key)
        if (candidate.source_tokens.get(key) is not None and value is None) or (
            value is not None and (not value.is_finite() or value < 0)
        ):
            add("INVALID_AMOUNT", f"Invalid source {key}", field=key)
    line_amounts = [
        line.source_line_total
        if line.source_line_total is not None
        else line.quantity * line.source_unit_price
        if line.quantity is not None and line.source_unit_price is not None
        else None
        for line in candidate.items
    ]
    subtotal, total = amounts.get("subtotal"), amounts.get("total")
    derived = (
        sum((v for v in line_amounts if v is not None), Decimal(0))
        if line_amounts and all(v is not None for v in line_amounts)
        else None
    )
    if subtotal is not None and derived is not None:
        performed.append("line_sum_subtotal")
        if abs(subtotal - derived) > policy.arithmetic_tolerance:
            add(
                "TOTAL_MISMATCH",
                "Line sum differs from source subtotal",
                origin="arithmetic",
                observed=str(subtotal),
                expected=str(derived),
            )
    else:
        unavailable.append("line_sum_subtotal")
    tax_rate, tax_amount = amounts.get("tax_rate"), amounts.get("tax_amount")
    if all(
        value is not None and value.is_finite() and value >= 0
        for value in (subtotal, tax_rate, tax_amount)
    ):
        assert subtotal is not None and tax_rate is not None and tax_amount is not None
        expected_tax = subtotal * tax_rate
        performed.append("subtotal_tax_rate")
        if abs(tax_amount - expected_tax) > policy.arithmetic_tolerance:
            add(
                "TOTAL_MISMATCH",
                "Stated tax differs from source subtotal times tax rate",
                field="tax_amount",
                origin="arithmetic",
                observed=str(tax_amount),
                expected=str(expected_tax),
            )
    elif candidate.source_tokens.get("tax_rate") is not None or tax_rate is not None:
        unavailable.append("subtotal_tax_rate")
    base = subtotal if subtotal is not None else derived
    if base is not None and total is not None:
        expected = (
            base
            + (amounts.get("tax_amount") or Decimal(0))
            + (amounts.get("shipping") or Decimal(0))
        )
        performed.append("payable_total")
        if abs(total - expected) > policy.arithmetic_tolerance:
            add(
                "TOTAL_MISMATCH",
                "Source subtotal plus stated charges differs from payable total",
                origin="arithmetic",
                observed=str(total),
                expected=str(expected),
            )
    else:
        unavailable.append("payable_total")
    if candidate.invoice_date and candidate.due_date:
        if candidate.due_date < candidate.invoice_date:
            add("DUE_BEFORE_ISSUE", "Due date precedes invoice date")
        if candidate.net_days is not None:
            try:
                expected_date = candidate.invoice_date + timedelta(days=candidate.net_days)
            except OverflowError:
                add("INVALID_PAYMENT_TERMS", "Net days exceeds supported date range")
            else:
                if expected_date != candidate.due_date:
                    add(
                        "TERMS_DATE_MISMATCH",
                        "Explicit due date conflicts with Net terms",
                        severity="warning",
                        observed=str(candidate.due_date),
                        expected=str(expected_date),
                    )
    return findings, performed, unavailable, demand


def validate_source(candidate: InvoiceCandidate, policy: Policy) -> list[ValidationFinding]:
    """Check source facts and arithmetic without accessing or assuming inventory."""
    return _source_validation(candidate, policy)[0]


def validate(
    candidate: InvoiceCandidate, snapshot: InventorySnapshot, policy: Policy
) -> ValidationReport:
    findings, performed, unavailable, demand = _source_validation(candidate, policy)
    for item, quantity in sorted(demand.items()):
        stock = snapshot.stock.get(item)
        if stock is None:
            finding = ValidationFinding(
                code="UNKNOWN_ITEM",
                message=f"{item} is not in inventory",
                origin=FindingOrigin.INVENTORY,
                severity=Severity.BLOCKER,
                field=item,
            )
        elif stock == 0:
            finding = ValidationFinding(
                code="OUT_OF_STOCK",
                message=f"{item} has zero stock",
                origin=FindingOrigin.INVENTORY,
                severity=Severity.BLOCKER,
                field=item,
            )
        elif quantity > stock:
            finding = ValidationFinding(
                code="INSUFFICIENT_STOCK",
                message=f"{item} requires {quantity}, available {stock}",
                origin=FindingOrigin.INVENTORY,
                severity=Severity.BLOCKER,
                field=item,
                observed=quantity,
                expected=stock,
            )
        else:
            continue
        findings.append(finding)
    performed.append("inventory")
    return ValidationReport(
        candidate_digest=candidate_digest(candidate),
        findings=findings,
        aggregate_quantities=demand,
        stock_snapshot=snapshot,
        performed_checks=performed,
        unavailable_checks=unavailable,
        complete=all(item in snapshot.stock for item in demand),
        requires_high_value_review=candidate.total_usd is not None
        and candidate.total_usd > policy.high_value_threshold,
    )
