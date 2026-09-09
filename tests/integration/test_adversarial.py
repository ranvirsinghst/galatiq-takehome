"""Cross-module regressions from independent adversarial review."""

from datetime import date
from decimal import Decimal

from invoice_agent.approval import review
from invoice_agent.config import Policy
from invoice_agent.database import SQLitePaymentStore
from invoice_agent.ingestion import _source_issues
from invoice_agent.models import (
    Evidence,
    InvoiceCandidate,
    InvoiceLine,
    LLMResponse,
    SourceDocument,
)
from invoice_agent.ports import NullEventSink
from invoice_agent.validation import validate


def test_table_quantity_misread_must_be_flagged():
    text = "WidgetA 20 10.00 200.00"
    source = SourceDocument(
        source_id="s",
        path="x.txt",
        format="txt",
        content_sha256="h",
        raw_text=text,
        evidence=[Evidence(evidence_id="row", source_id="s", location="line:1", excerpt=text)],
    )
    # Substring membership is insufficient: "2" occurs in "20".
    data = {"line_items": [{"item": "WidgetA", "quantity": "2", "evidence_refs": ["row"]}]}
    assert _source_issues(data, source), (
        "Table quantity changed from 20 to 2 without extraction repair"
    )


def test_model_semantic_failure_still_cannot_authorize_payment(tmp_path):
    store = SQLitePaymentStore(tmp_path / "r.db", "r")
    c = InvoiceCandidate(
        source_id="s",
        vendor_normalized="Vendor",
        invoice_number_normalized="INV-1",
        invoice_date=date(2026, 1, 1),
        due_date=date(2026, 2, 1),
        total_usd=Decimal(10),
        source_currency="USD",
        fx_rate_to_usd=Decimal(1),
        source_amounts={"total": Decimal(10)},
        items=[
            InvoiceLine(
                line_id="1",
                item_name_normalized="WidgetA",
                quantity_raw="1",
                quantity=Decimal(1),
                source_unit_price=Decimal(10),
                unit_price_usd=Decimal(10),
            )
        ],
    )
    report = validate(c, store.lookup(["WidgetA"]), Policy(), store.lookup_price(["WidgetA"]))

    class HostileVP:
        def complete(self, request):
            if request.phase == "vp_critique":
                return LLMResponse(
                    content={
                        "verdict": "accept",
                        "reason_summary": "The proposal follows the supplied validation findings.",
                    }
                )
            return LLMResponse(
                content={
                    "decision": "rejected",
                    "reason_summary": "Vendor is definitely committing fraud.",
                    "checks": {"unavailable_checks": "Unavailable."},
                }
            )

    outcome = review(c, report, HostileVP(), Policy(), NullEventSink())
    # Semantic quality is explicitly model-dependent; record that mechanical guards
    # cannot prove a rationale. This asserts no accidental payment authority.
    assert outcome.proposal.decision == "rejected"
    store.close()


def test_nested_candidate_mutation_invalidates_accepted_review(tmp_path):
    import pytest

    from invoice_agent.models import Critique, Proposal, ReviewOutcome, candidate_digest
    from invoice_agent.payment import build_payment_request

    store = SQLitePaymentStore(tmp_path / "mutation.db", "r")
    c = InvoiceCandidate(
        source_id="s",
        vendor_normalized="Vendor",
        invoice_number_normalized="INV-1",
        invoice_date=date(2026, 1, 1),
        due_date=date(2026, 2, 1),
        total_usd=Decimal(10),
        source_currency="USD",
        fx_rate_to_usd=Decimal(1),
        source_amounts={"total": Decimal(10)},
        items=[
            InvoiceLine(
                line_id="1",
                item_name_normalized="WidgetA",
                quantity_raw="1",
                quantity=Decimal(1),
                source_unit_price=Decimal(10),
                unit_price_usd=Decimal(10),
            )
        ],
    )
    report = validate(c, store.lookup(["WidgetA"]), Policy(), store.lookup_price(["WidgetA"]))
    outcome = ReviewOutcome(
        candidate_digest=candidate_digest(c),
        accepted=True,
        proposal=Proposal(
            decision="approved",
            reason_summary="Valid",
            checks={"unavailable_checks": "Optional amounts absent."},
        ),
        critique=Critique(verdict="accept"),
    )
    # Pydantic frozen models do not freeze nested containers. Digest must catch it.
    c.assumptions.append("Changed source interpretation after approval")
    with pytest.raises(ValueError):
        build_payment_request(c, report, outcome, "r")
    assert store.snapshot().stock["WidgetA"] == 15
    store.close()


def test_synthetic_report_cannot_hide_policy_warnings(tmp_path):
    from invoice_agent.models import Critique, Proposal, ReviewOutcome, candidate_digest
    from invoice_agent.payment import build_payment_request

    store = SQLitePaymentStore(tmp_path / "forged.db", "r")
    c = InvoiceCandidate(
        source_id="s",
        vendor_normalized="Vendor",
        invoice_number_normalized="INV-1",
        invoice_date=date(2026, 1, 1),
        due_date=date(2026, 2, 1),
        net_days=1,
        total_usd=Decimal(10),
        source_currency="USD",
        fx_rate_to_usd=Decimal(1),
        source_amounts={"total": Decimal(10)},
        items=[
            InvoiceLine(
                line_id="1",
                item_name_normalized="WidgetA",
                quantity_raw="1",
                quantity=Decimal(1),
                source_unit_price=Decimal(10),
                unit_price_usd=Decimal(10),
            )
        ],
    )
    actual = validate(c, store.lookup(["WidgetA"]), Policy(), store.lookup_price(["WidgetA"]))
    assert any(f.code == "TERMS_DATE_MISMATCH" for f in actual.findings)
    forged = actual.model_copy(update={"findings": []})
    outcome = ReviewOutcome(
        candidate_digest=candidate_digest(c),
        accepted=True,
        proposal=Proposal(
            decision="approved",
            reason_summary="Valid",
            checks={"unavailable_checks": "Optional amounts absent."},
        ),
        critique=Critique(verdict="accept"),
    )
    request = build_payment_request(c, forged, outcome, "r")

    def never_pay(*args):
        raise AssertionError("Forged report reached mock payment")

    result = store.pay(request, never_pay)
    assert result.status == "not_paid"
    assert result.findings[0].code == "INVALID_PAYMENT_REQUEST"
    store.close()
