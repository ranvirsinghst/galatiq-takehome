"""Explicit live provider gate; opt in with RUN_LIVE_XAI=1 and XAI_API_KEY."""

import os
from pathlib import Path

import pytest

from invoice_agent.config import Policy, load_settings
from invoice_agent.database import SQLitePaymentStore
from invoice_agent.llm import XAIClient

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.getenv("RUN_LIVE_XAI") != "1",
        reason="Set RUN_LIVE_XAI=1 to execute paid provider requests",
    ),
]


def test_live_messy_extraction_inventory_and_deterministic_rejection(tmp_path):
    from invoice_agent.output import EventCollector
    from invoice_agent.runner import RunDependencies, run

    settings = load_settings(Path(".env"))
    events = EventCollector("live-reject", secrets=[settings.api_key.get_secret_value()])
    llm = XAIClient(
        settings.api_key.get_secret_value(),
        model=settings.model,
        base_url=settings.base_url,
        timeout=settings.timeout_seconds,
        events=events,
        run_id=events.run_id,
    )
    store = SQLitePaymentStore(tmp_path / "live.db", events.run_id, events=events)
    try:
        result = run(
            [Path("data/invoices/invoice_1002.txt")], RunDependencies(store, llm, events, Policy())
        )
        assert result.summary.operational_errors == 0
        item = result.results[0]
        assert item.decision == "rejected" and item.payment.status == "not_paid"
        assert any(f.code == "INSUFFICIENT_STOCK" for f in item.findings)
        assert any(f.code == "TERMS_DATE_MISMATCH" for f in item.findings)
        assert item.review is None and item.validation is not None
        assert item.attempts["vp_calls"] == 0
        names = {e.event for e in result.trace}
        assert {"tool_requested", "review_rejected_by_rules"} <= names
        assert not {"vp_propose", "vp_critique", "vp_revise", "payment_committed"} & names
        assert result.summary.new_payments == 0
        assert store.snapshot().stock["GadgetX"] == 5
    finally:
        store.close()
        llm.close()


def test_live_clean_invoice_runs_through_committed_mock_payment(tmp_path):
    from invoice_agent.output import EventCollector
    from invoice_agent.runner import RunDependencies, run

    settings = load_settings(Path(".env"))
    events = EventCollector("live-clean", secrets=[settings.api_key.get_secret_value()])
    llm = XAIClient(
        settings.api_key.get_secret_value(),
        model=settings.model,
        base_url=settings.base_url,
        timeout=settings.timeout_seconds,
        events=events,
        run_id="live-clean",
    )
    store = SQLitePaymentStore(tmp_path / "live-clean.db", "live-clean", events=events)
    try:
        result = run(
            [Path("data/invoices/invoice_1001.txt")], RunDependencies(store, llm, events, Policy())
        )
        assert result.summary.operational_errors == 0
        assert result.summary.new_payments == 1
        assert result.results[0].payment.status == "paid"
        assert result.results[0].payment.payment_id
        assert result.summary.final_inventory["WidgetA"] < 15
        names = {event.event for event in result.trace}
        assert {"tool_requested", "vp_propose", "vp_critique", "payment_committed"} <= names
        prices = {
            item: value
            for event in result.trace
            if event.event == "tool_result" and event.payload.get("name") == "lookup_price"
            for item, value in event.payload.get("prices", {}).items()
        }
        assert prices == {"WidgetA": "250.00", "WidgetB": "500.00"}
        assert store.find_paid(result.results[0].identity) is not None
    finally:
        llm.close()
        store.close()
