"""Explicit live provider gate; opt in with RUN_LIVE_XAI=1 and XAI_API_KEY."""

import os
from pathlib import Path

import pytest

from invoice_agent.approval import review
from invoice_agent.config import Policy, load_settings
from invoice_agent.database import SQLitePaymentStore
from invoice_agent.ingestion import ingest
from invoice_agent.llm import XAIClient
from invoice_agent.readers import read_source
from invoice_agent.tools import validate_with_tools

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.getenv("RUN_LIVE_XAI") != "1",
        reason="Set RUN_LIVE_XAI=1 to execute paid provider requests",
    ),
]


class Events:
    def __init__(self):
        self.events = []

    def emit(self, event):
        self.events.append(event)


def test_live_messy_extraction_inventory_tools_and_reflection(tmp_path):
    settings = load_settings(Path(".env"))
    events = Events()
    llm = XAIClient(
        settings.api_key.get_secret_value(),
        model=settings.model,
        base_url=settings.base_url,
        events=events,
        run_id="live",
    )
    store = SQLitePaymentStore(tmp_path / "live.db", "live")
    try:
        source = read_source(Path("data/invoices/invoice_1002.txt"), "s")
        ingested = ingest(source, llm, Policy(), events)
        assert ingested.error is None and ingested.candidate is not None
        validated = validate_with_tools(ingested.candidate, store, llm, Policy(), events)
        assert validated.error is None and validated.report is not None
        assert any(f.code == "INSUFFICIENT_STOCK" for f in validated.report.findings)
        reviewed = review(ingested.candidate, validated.report, llm, Policy(), events)
        assert reviewed.error is None and reviewed.accepted
        assert reviewed.proposal.decision == "rejected"
        names = {event.event for event in events.events}
        assert {"tool_requested", "vp_propose", "vp_critique"} <= names
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
        assert store.find_paid(result.results[0].identity) is not None
    finally:
        llm.close()
        store.close()
