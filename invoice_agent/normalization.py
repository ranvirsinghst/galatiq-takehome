"""Conservative, auditable normalization; invalid tokens remain available to callers."""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from .models import FindingOrigin, Severity
from .money import parse_decimal, round_cents


def normalize_decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    cleaned = str(value).strip()
    cleaned = re.sub(r"^(?:USD|EUR|\$|€)\s*", "", cleaned, flags=re.I)
    if "," in cleaned:
        if not re.fullmatch(r"[+-]?[0-9O]{1,3}(?:,[0-9O]{3})+(?:\.[0-9O]+)?", cleaned):
            return None
        cleaned = cleaned.replace(",", "")
    # Restrict O/0 repair to a token otherwise shaped like a decimal number.
    if re.fullmatch(r"[+-]?[0-9O]+(?:\.[0-9O]+)?", cleaned):
        cleaned = cleaned.replace("O", "0")
    try:
        return parse_decimal(cleaned)
    except ValueError:
        return None


def normalize_date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    cleaned = re.sub(r"(?<!\w)([12][0-9O]{3})(?!\w)", lambda m: m[0].replace("O", "0"), cleaned)
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d-%b-%Y", "%B %d, %Y", "%b %d %Y", "%B %d %Y"):
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    return None


def normalize_tax_rate(value: Any) -> Decimal | None:
    """Interpret an explicit percent suffix; unsuffixed rates remain fractions."""
    if isinstance(value, str) and "%" in value:
        token = value.strip()
        if not re.fullmatch(r"[+-]?[0-9O]+(?:\.[0-9O]+)?\s*%", token):
            return None
        parsed = normalize_decimal(token[:-1].strip())
        if parsed is None:
            return None
        try:
            round_cents(parsed)
        except ValueError:
            return None
        return parsed / Decimal(100)
    return normalize_decimal(value)


def normalize_invoice_number(value: Any) -> str | None:
    if value is None or not str(value).strip():
        return None
    value = str(value).strip().upper()
    match = re.fullmatch(r"(?:INV[\s-]*)?(\d+)", value)
    return f"INV-{match[1]}" if match else value


def normalize_item(value: Any, aliases: dict[str, str] | None = None) -> str | None:
    if value is None or not str(value).strip():
        return None
    value = str(value).strip()
    # Parenthetical qualifiers remain in description_raw; only the stock key drops them.
    base = re.sub(r"\s*\([^)]*\)\s*$", "", value)
    key = re.sub(r"\s+", "", base).casefold()
    known = {"widgeta": "WidgetA", "widgetb": "WidgetB", "gadgetx": "GadgetX"}
    if aliases:
        known.update({re.sub(r"\s+", "", k).casefold(): v for k, v in aliases.items()})
    return known.get(key, base)


def candidate_from_mapping(data, source, policy):
    """Map source-level fields without imposing payable-invoice constraints."""
    from .models import InvoiceCandidate, InvoiceLine, Normalization, ValidationFinding
    from .money import convert_to_usd

    notes = []
    findings = list(source.reader_warnings)
    refs = data.get("field_evidence") or {}
    evidence_ids = [e.evidence_id for e in source.evidence]

    def normalized(field, raw, value, method):
        if raw is not None and str(raw) != str(value) and value is not None:
            notes.append(
                Normalization(
                    field=field,
                    original_value=raw,
                    normalized_value=str(value),
                    method=method,
                    evidence_ids=refs.get(field, evidence_ids),
                )
            )
        return value

    currency_raw = data.get("currency")
    currency = str(currency_raw).strip().upper() if currency_raw else "USD"
    rates = getattr(policy, "fx_rates", {"USD": Decimal("1"), "EUR": Decimal("1.10")})
    rate = rates.get(currency)
    assumptions = [] if currency_raw else ["Missing currency assumed USD"]
    if rate is None:
        findings.append(
            ValidationFinding(
                code="UNSUPPORTED_CURRENCY",
                severity=Severity.BLOCKER,
                origin=FindingOrigin.SOURCE,
                field="currency",
                message=f"Unsupported currency: {currency}",
            )
        )

    def amount(value, field):
        parsed = normalize_tax_rate(value) if field == "tax_rate" else normalize_decimal(value)
        if parsed is not None:
            try:
                round_cents(parsed)
                if rate is not None:
                    convert_to_usd(parsed, rate)
            except ValueError:
                findings.append(
                    ValidationFinding(
                        code="INVALID_AMOUNT",
                        severity=Severity.BLOCKER,
                        origin=FindingOrigin.SOURCE,
                        field=field,
                        message="Source numeric amount exceeds supported monetary range",
                        observed=str(value),
                    )
                )
                parsed = None
        method = (
            "explicit percentage to decimal rate"
            if field == "tax_rate" and isinstance(value, str) and "%" in value
            else "decimal token cleanup"
        )
        return normalized(field, value, parsed, method)

    def usd(value):
        return convert_to_usd(value, rate) if value is not None and rate is not None else None

    lines = []
    for index, row in enumerate(data.get("line_items") or []):
        description = row.get("item")
        price = amount(row.get("unit_price"), f"items.{index}.unit_price")
        total = amount(row.get("amount"), f"items.{index}.amount")
        item = normalized(
            f"items.{index}.item",
            description,
            normalize_item(description, getattr(policy, "item_aliases", None)),
            "conservative inventory alias",
        )
        lines.append(
            InvoiceLine(
                line_id=f"line-{index + 1}",
                description_raw=None if description is None else str(description),
                item_name_normalized=item,
                quantity_raw=row.get("quantity"),
                quantity=amount(row.get("quantity"), f"items.{index}.quantity"),
                source_unit_price=price,
                source_line_total=total,
                unit_price_usd=usd(price),
                line_total_usd=usd(total),
                source_tokens=row,
                evidence_refs=row.get("evidence_refs") or evidence_ids,
            )
        )
        if row.get("quantity") is None:
            findings.append(
                ValidationFinding(
                    code="MISSING_REQUIRED_FIELD",
                    severity=Severity.BLOCKER,
                    origin=FindingOrigin.SOURCE,
                    field=f"items.{index}",
                    message="Incomplete source item group",
                    line_ids=[f"line-{index + 1}"],
                )
            )
    amounts = {
        key: amount(data.get(key), key)
        for key in ("subtotal", "tax_amount", "shipping", "total", "tax_rate")
    }
    vendor = data.get("vendor")
    if isinstance(vendor, dict):
        vendor = vendor.get("name")
    vendor = None if vendor is None else str(vendor)
    invoice_raw = None if data.get("invoice_number") is None else str(data["invoice_number"])
    invoice_date_raw = None if data.get("date") is None else str(data["date"])
    due_raw = None if data.get("due_date") is None else str(data["due_date"])
    if due_raw and normalize_date(due_raw) is None:
        findings.append(
            ValidationFinding(
                code="INVALID_DATE",
                severity=Severity.BLOCKER,
                origin=FindingOrigin.SOURCE,
                field="due_date",
                message="Source due date cannot be resolved",
                observed=due_raw,
                evidence_refs=refs.get("due_date", evidence_ids),
            )
        )
    terms = data.get("payment_terms")
    days = re.search(r"\bNet\s+(\d+)\b", str(terms), re.I)
    return InvoiceCandidate(
        source_id=source.source_id,
        invoice_number_raw=invoice_raw,
        invoice_number_normalized=normalized(
            "invoice_number",
            invoice_raw,
            normalize_invoice_number(invoice_raw),
            "invoice identity formatting",
        ),
        revision=data.get("revision"),
        vendor_raw=vendor,
        vendor_normalized=vendor.strip() if vendor else vendor,
        invoice_date_raw=invoice_date_raw,
        invoice_date=normalized(
            "date",
            invoice_date_raw,
            normalize_date(invoice_date_raw),
            "explicit date parsing/OCR year repair",
        ),
        due_date_raw=due_raw,
        due_date=normalized("due_date", due_raw, normalize_date(due_raw), "explicit date parsing"),
        payment_terms_raw=terms,
        net_days=int(days[1]) if days else None,
        source_currency=currency,
        fx_rate_to_usd=rate,
        items=lines,
        subtotal_usd=usd(amounts["subtotal"]),
        tax_usd=usd(amounts["tax_amount"]),
        shipping_usd=usd(amounts["shipping"]),
        total_usd=usd(amounts["total"]),
        source_amounts=amounts,
        source_tokens=data,
        evidence=source.evidence,
        field_evidence=refs
        or {key: evidence_ids for key in ("vendor", "invoice_number", "date", "due_date", "total")},
        normalizations=notes,
        assumptions=assumptions,
        findings=findings,
    )
