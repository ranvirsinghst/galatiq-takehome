from decimal import Decimal

import pytest

from invoice_agent.config import Policy
from invoice_agent.database import SQLitePaymentStore
from invoice_agent.graph import process_invoice
from invoice_agent.output import EventCollector
from invoice_agent.payment import mock_payment
from tests.doubles import ScenarioLLM
from tests.unit.test_validation import candidate


@pytest.mark.parametrize("changed_source", ["shipping", "tax_rate", "warning", "unchanged"])
def test_duplicate_checks_source_without_requiring_depleted_stock(tmp_path, changed_source):
    policy = Policy()
    events = EventCollector("run")
    store = SQLitePaymentStore(tmp_path / "inventory.db", "run")
    line = (
        candidate()
        .items[0]
        .model_copy(
            update={
                "quantity_raw": 15,
                "quantity": Decimal(15),
                "source_line_total": Decimal(150),
            }
        )
    )
    c = candidate(
        items=[line],
        total_usd=Decimal(150),
        source_amounts={
            "subtotal": Decimal(150),
            "total": Decimal(150),
        },
    )
    try:
        first = process_invoice(c, "first", 0, store, ScenarioLLM(), policy, events, mock_payment)
        assert first.payment.status == "paid"
        assert store.snapshot().stock["WidgetA"] == 0
        changes = {"source_id": "second"}
        if changed_source in {"shipping", "tax_rate"}:
            changes.update(
                source_tokens={changed_source: "broken"},
                source_amounts={**c.source_amounts, changed_source: None},
            )
        elif changed_source == "warning":
            changes["assumptions"] = ["Missing currency assumed USD"]
        llm = ScenarioLLM()
        result = process_invoice(
            c.model_copy(update=changes),
            "second",
            1,
            store,
            llm,
            policy,
            events,
            lambda *_: pytest.fail("Duplicate must never pay again"),
        )
        if changed_source in {"shipping", "tax_rate"}:
            assert result.decision == "rejected"
            assert any(
                f.code == "INVALID_AMOUNT" and f.field == changed_source for f in result.findings
            )
        else:
            assert result.payment.status == "already_paid"
            assert bool(result.findings) == (changed_source == "warning")
        assert not llm.requests
        assert store.snapshot().generation == 1
        assert store.snapshot().stock["WidgetA"] == 0
    finally:
        store.close()
