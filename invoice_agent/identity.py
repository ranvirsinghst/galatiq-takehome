"""Versioned, conservative invoice payment equivalence."""

import hashlib
import json
import re
from decimal import Decimal

from .models import InvoiceCandidate, InvoiceIdentity


def payment_identity(candidate: InvoiceCandidate) -> InvoiceIdentity | None:
    vendor = candidate.vendor_normalized or candidate.vendor_raw
    number = candidate.invoice_number_normalized or candidate.invoice_number_raw
    if not vendor or not number:
        return None
    vendor = " ".join(vendor.split()).casefold()
    number = number.strip().upper()
    match = re.fullmatch(r"INV[ -]*(\d+)", number)
    if match:
        number = "INV-" + match[1]
    return InvoiceIdentity(vendor=vendor, invoice_number=number) if vendor and number else None


def _decimal(value):
    return format(value.normalize(), "f") if value is not None else None


def payment_fingerprint(candidate: InvoiceCandidate) -> str | None:
    identity = payment_identity(candidate)
    if (
        identity is None
        or candidate.total_usd is None
        or not candidate.invoice_date
        or not candidate.due_date
        or not candidate.items
    ):
        return None
    lines = []
    derived = Decimal(0)
    derivable = True
    for line in candidate.items:
        if (
            not line.item_name_normalized
            or line.quantity is None
            or isinstance(line.quantity_raw, bool)
        ):
            return None
        amount = line.source_line_total
        if amount is None and line.source_unit_price is not None:
            amount = line.quantity * line.source_unit_price
        if amount is None:
            derivable = False
        else:
            derived += amount
        lines.append(
            [
                line.item_name_normalized,
                _decimal(line.quantity),
                _decimal(line.source_unit_price),
                _decimal(amount),
            ]
        )
    subtotal = candidate.source_amounts.get("subtotal")
    if subtotal is None and derivable:
        subtotal = derived
    tax = candidate.source_amounts.get("tax_amount")
    shipping = candidate.source_amounts.get("shipping")
    total = candidate.source_amounts.get("total")
    if (
        subtotal is not None
        and total is not None
        and all(v is None or v >= 0 for v in (tax, shipping))
        and total == subtotal + (tax or Decimal(0)) + (shipping or Decimal(0))
    ):
        tax, shipping = tax or Decimal(0), shipping or Decimal(0)
    payload = dict(
        version=1,
        identity=identity.model_dump(),
        date=str(candidate.invoice_date),
        due=str(candidate.due_date),
        currency=candidate.source_currency,
        fx=_decimal(candidate.fx_rate_to_usd),
        payable_usd=_decimal(candidate.total_usd),
        subtotal=_decimal(subtotal),
        tax=_decimal(tax),
        shipping=_decimal(shipping),
        lines=sorted(lines, key=lambda x: json.dumps(x)),
    )
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
