from datetime import date
from decimal import Decimal

import pytest

from invoice_agent.approval import review
from invoice_agent.config import Policy
from invoice_agent.database import SQLitePaymentStore
from invoice_agent.models import InvoiceCandidate, InvoiceLine, LLMResponse, MockPaymentResult
from invoice_agent.payment import build_payment_request
from invoice_agent.ports import NullEventSink
from invoice_agent.validation import validate


class EvidenceVP:
    def complete(self, request):
        import json

        facts = json.loads(request.messages[-1]["content"])
        if request.phase == "vp_critique":
            return LLMResponse(
                content={
                    "verdict": "accept",
                    "reason_summary": "The proposal follows the supplied validation findings.",
                }
            )
        findings = facts["validation"]["findings"]
        blocked = any(f["severity"] == "blocker" for f in findings)
        return LLMResponse(
            content={
                "decision": "rejected" if blocked else "approved",
                "reason_summary": "Decision follows validation evidence.",
                "finding_codes": [f["code"] for f in findings],
                "checks": {"unavailable_checks": "Optional arithmetic inputs were absent."},
            }
        )


@pytest.mark.parametrize(
    "item,quantity,paid",
    [("WidgetA", "1", True), ("WidgetA", "-1", False), ("Unknown", "1", False)],
)
def test_real_validator_review_and_payment_gate(tmp_path, item, quantity, paid):
    policy = Policy()
    store = SQLitePaymentStore(tmp_path / "run.db", "r")
    c = InvoiceCandidate(
        source_id="s",
        vendor_raw="Vendor",
        vendor_normalized="vendor",
        invoice_number_raw="INV-1",
        invoice_number_normalized="INV-1",
        invoice_date=date(2026, 1, 1),
        due_date=date(2026, 2, 1),
        source_currency="USD",
        fx_rate_to_usd=Decimal("1"),
        total_usd=Decimal("10"),
        source_amounts={"total": Decimal("10")},
        items=[
            InvoiceLine(
                line_id="1",
                description_raw=item,
                item_name_normalized=item,
                quantity_raw=quantity,
                quantity=Decimal(quantity),
            )
        ],
    )
    report = validate(c, store.lookup([item]), policy)
    outcome = review(c, report, EvidenceVP(), policy, NullEventSink())
    assert outcome.accepted
    calls = []

    def mock(vendor, amount):
        calls.append((vendor, amount))
        return MockPaymentResult(success=True, payment_id="p")

    if paid:
        result = store.pay(build_payment_request(c, report, outcome, "r"), mock)
        assert result.status == "paid"
        assert store.snapshot().stock["WidgetA"] == 14
    else:
        with pytest.raises(ValueError):
            build_payment_request(c, report, outcome, "r")
    assert len(calls) == int(paid)
    store.close()
