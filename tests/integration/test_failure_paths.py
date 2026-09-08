"""Cross-boundary regressions from independent integration review."""

import json

import httpx

from invoice_agent.config import Policy
from invoice_agent.database import SQLitePaymentStore
from invoice_agent.errors import AgentError
from invoice_agent.llm import XAIClient
from invoice_agent.models import ErrorCode
from invoice_agent.output import EventCollector
from invoice_agent.runner import RunDependencies, run
from invoice_agent.tools import validate_with_tools
from tests.doubles import ScenarioLLM
from tests.integration.test_batch import execute, invoice
from tests.unit.test_validation import candidate


def test_real_adapter_invalid_tool_response_recovers_within_graph(tmp_path):
    attempts = []

    def endpoint(request):
        attempts.append(request)
        name = "pay_invoice" if len(attempts) == 1 else "lookup_inventory"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "tool_calls": [
                                {
                                    "id": f"call-{len(attempts)}",
                                    "function": {
                                        "name": name,
                                        "arguments": json.dumps({"items": ["WidgetA"]}),
                                    },
                                }
                            ]
                        },
                    }
                ]
            },
        )

    store = SQLitePaymentStore(tmp_path / "db", "run")
    llm = XAIClient("test-key", transport=httpx.MockTransport(endpoint))
    try:
        result = validate_with_tools(candidate(), store, llm, Policy(), EventCollector("run"))
        assert result.error is None
        assert result.tool_rounds == 2
        assert result.report.complete
        assert len(attempts) == 2
    finally:
        llm.close()
        store.close()


def test_disappeared_file_is_operational_and_healthy_invoice_continues(tmp_path):
    missing = tmp_path / "gone.json"
    good = invoice(tmp_path / "good.json", "INV-1", "2026-01-01")
    result = execute([missing, good], tmp_path)
    assert result.summary.operational_errors == 1
    assert result.summary.new_payments == 1
    assert result.results[0].error.code == "SOURCE_IO_ERROR"
    assert result.results[0].decision is None


def test_missing_date_rejected_before_ordering(tmp_path):
    bad = invoice(tmp_path / "bad.json", "INV-2", "2026-01-01")
    value = json.loads(bad.read_text())
    value.pop("date")
    bad.write_text(json.dumps(value))
    good = invoice(tmp_path / "good.json", "INV-1", "2026-01-01")
    result = execute([bad, good], tmp_path)
    rejected = next(r for r in result.results if r.decision == "rejected")
    assert rejected.processing_index is None
    assert "INVALID_DATE" in [f.code for f in rejected.findings]
    assert result.summary.new_payments == 1


def test_fatal_provider_stops_remaining_model_work(tmp_path):
    class FailedProvider:
        def __init__(self):
            self.calls = 0

        def complete(self, request):
            self.calls += 1
            raise AgentError(ErrorCode.PROVIDER_PERMANENT, "Authentication rejected", fatal=True)

    llm = FailedProvider()
    first = invoice(tmp_path / "a.json", "INV-1", "2026-01-01")
    second = invoice(tmp_path / "b.json", "INV-2", "2026-01-02")
    result = execute([first, second], tmp_path, llm=llm)
    assert llm.calls == 1
    assert result.summary.operational_errors == 2
    assert result.summary.new_payments == 0


def test_fatal_storage_retains_invoice_results(tmp_path):
    class BrokenStore(SQLitePaymentStore):
        def find_paid(self, identity):
            raise AgentError(ErrorCode.STORAGE_ERROR, "Database unusable", fatal=True)

        def snapshot(self):
            raise AgentError(ErrorCode.STORAGE_ERROR, "Database unusable", fatal=True)

    store = BrokenStore(tmp_path / "db", "run")
    first = invoice(tmp_path / "a.json", "INV-1", "2026-01-01")
    second = invoice(tmp_path / "b.json", "INV-2", "2026-01-02")
    try:
        result = run(
            [first, second], RunDependencies(store, ScenarioLLM(), EventCollector("run"), Policy())
        )
        assert len(result.results) == 2
        assert result.summary.operational_errors == 2
        assert all(r.error.code == "STORAGE_ERROR" for r in result.results)
    finally:
        store.close()


def test_cli_directory_permission_failure_is_structured(monkeypatch, capsys):
    import main

    def denied(path):
        raise PermissionError("sensitive filesystem details")

    monkeypatch.setattr(main, "discover", denied)
    assert main.main(["--invoice_path", "anything"]) != 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "sensitive filesystem details" not in captured.err
    assert json.loads(captured.err)["error"] in {"SOURCE_IO_ERROR", "CONFIG_ERROR", "RUN_FAILED"}


def test_one_commit_event_per_new_payment(tmp_path):
    good = invoice(tmp_path / "good.json", "INV-1", "2026-01-01")
    result = execute([good], tmp_path)
    assert len([e for e in result.trace if e.event == "payment_committed"]) == 1
