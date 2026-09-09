import io
import json

from invoice_agent.models import TraceEvent
from invoice_agent.output import EventCollector, write_jsonl
from tests.integration.test_batch import execute, invoice


def test_jsonl_trace_and_summary(tmp_path):
    good = invoice(tmp_path / "a.json", "INV-1", "2026-01-01")
    result = execute([good], tmp_path)
    plain, traced = io.StringIO(), io.StringIO()
    write_jsonl(result, plain)
    write_jsonl(result, traced, trace=True)
    rows = [json.loads(s) for s in plain.getvalue().splitlines()]
    trace_rows = [json.loads(s) for s in traced.getvalue().splitlines()]
    assert [r["type"] for r in rows] == ["invoice", "summary"]
    assert "trace" not in rows[0] and trace_rows[0]["trace"]
    assert rows[0]["reasons"] == trace_rows[0]["reasons"]
    assert rows[1]["new_payments"] == 1


def test_collector_redacts_literal_credentials():
    observed = []
    events = EventCollector("run", secrets=["xai-a-real-test-sentinel"], on_event=observed.append)
    events.emit(
        TraceEvent(
            run_id="",
            stage="test",
            event="test",
            payload={"message": "contains xai-a-real-test-sentinel"},
        )
    )
    assert "xai-a-real-test-sentinel" not in events.events[0].model_dump_json()
    assert observed == events.events


def test_cli_invalid_path_and_missing_key(tmp_path, monkeypatch, capsys):
    import main

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    assert main.main(["--invoice_path", "absent.json"]) == 2
    path = invoice(tmp_path / "a.json", "INV-1", "2026-01-01")
    assert main.main(["--invoice_path", str(path)]) == 2
    assert capsys.readouterr().out == ""


def test_real_cli_wiring_with_injected_model(tmp_path, monkeypatch, capsys):
    import main
    from tests.doubles import ScenarioLLM

    class LocalModel(ScenarioLLM):
        def __init__(self, *args, **kwargs):
            super().__init__()

        def close(self):
            pass

    monkeypatch.setenv("XAI_API_KEY", "test-only-placeholder")
    monkeypatch.setattr(main, "XAIClient", LocalModel)
    path = invoice(tmp_path / "a.json", "INV-1", "2026-01-01")
    assert (
        main.main(
            [
                "--invoice_path",
                str(path),
                "--output_dir",
                str(tmp_path / "runs"),
                "--trace",
                "--json",
            ]
        )
        == 0
    )
    rows = [json.loads(s) for s in capsys.readouterr().out.splitlines()]
    assert rows[-1]["new_payments"] == 1
    assert len(list((tmp_path / "runs").glob("*/inventory.db"))) == 1


def test_cli_summary_only_failure_returns_nonzero(tmp_path, monkeypatch, capsys):
    import main
    from invoice_agent.database import SQLitePaymentStore
    from invoice_agent.errors import AgentError
    from invoice_agent.models import ErrorCode
    from tests.doubles import ScenarioLLM

    class LocalModel(ScenarioLLM):
        def __init__(self, *args, **kwargs):
            super().__init__()

        def close(self):
            pass

    class FinalSnapshotFails(SQLitePaymentStore):
        def snapshot(self):
            raise AgentError(ErrorCode.STORAGE_ERROR, "Final inventory unavailable", fatal=True)

    monkeypatch.setenv("XAI_API_KEY", "test-only-placeholder")
    monkeypatch.setattr(main, "XAIClient", LocalModel)
    monkeypatch.setattr(main, "SQLitePaymentStore", FinalSnapshotFails)
    path = invoice(tmp_path / "a.json", "INV-1", "2026-01-01")
    assert (
        main.main(["--invoice_path", str(path), "--output_dir", str(tmp_path / "runs"), "--json"])
        == 1
    )
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert rows[0]["payment"]["status"] == "paid"
    assert rows[-1]["operational_errors"] == 0
    assert rows[-1]["error"]["code"] == "STORAGE_ERROR"
    assert rows[-1]["final_inventory"] == {}


def test_human_cli_streams_before_source_read_and_keeps_json_artifacts(tmp_path, monkeypatch):
    import invoice_agent.runner as runner
    import main
    from tests.doubles import ScenarioLLM

    out, progress = io.StringIO(), io.StringIO()
    monkeypatch.setattr(main.sys, "stdout", out)
    monkeypatch.setattr(main.sys, "stderr", progress)
    monkeypatch.setenv("XAI_API_KEY", "test-only-placeholder")
    folder = tmp_path / "invoices"
    folder.mkdir()
    invoice(folder / "a.json", "INV-1", "2026-01-02")
    invoice(folder / "b.json", "INV-2", "2026-01-01")
    original = runner.read_source

    def observing_reader(path, source_id):
        assert f"{path.name}: Reading invoice" in progress.getvalue()
        return original(path, source_id)

    class LocalModel(ScenarioLLM):
        def __init__(self, *args, **kwargs):
            super().__init__()

        def complete(self, request):
            assert "a.json: Reading invoice" in progress.getvalue()
            assert "b.json: Reading invoice" in progress.getvalue()
            return super().complete(request)

        def close(self):
            pass

    monkeypatch.setattr(runner, "read_source", observing_reader)
    monkeypatch.setattr(main, "XAIClient", LocalModel)
    assert main.main(["--invoice_path", str(folder), "--output_dir", str(tmp_path / "runs")]) == 0
    assert "Paid (simulated)" in out.getvalue()
    assert out.getvalue().index("b.json") < out.getvalue().index("a.json")
    assert '"type":' not in out.getvalue()
    artifact = next((tmp_path / "runs").glob("*/results.jsonl"))
    rows = [json.loads(line) for line in artifact.read_text().splitlines()]
    assert rows[-1]["type"] == "summary"
    assert rows[-1]["new_payments"] == 2


def test_unavailable_progress_does_not_change_payment(tmp_path):
    from invoice_agent.config import Policy
    from invoice_agent.database import SQLitePaymentStore
    from invoice_agent.runner import RunDependencies, run
    from tests.doubles import ScenarioLLM

    def broken_progress(event):
        raise OSError("stderr disconnected")

    events = EventCollector("progress-failure", on_event=broken_progress)
    path = invoice(tmp_path / "a.json", "INV-1", "2026-01-01")
    store = SQLitePaymentStore(tmp_path / "run.db", "progress-failure")
    try:
        result = run([path], RunDependencies(store, ScenarioLLM(), events, Policy()))
    finally:
        store.close()
    assert result.summary.new_payments == 1
    assert result.summary.operational_errors == 0
    assert sum(e.event == "progress_output_unavailable" for e in events.events) == 1


def test_human_diagnostic_cannot_inject_terminal_lines(monkeypatch, capsys):
    import main

    def bad_path(path):
        raise ValueError("bad\npath\x1b[31m")

    monkeypatch.setattr(main, "discover", bad_path)
    assert main.main(["--invoice_path", "unused"]) == 2
    err = capsys.readouterr().err
    assert len(err.splitlines()) == 1 and "\x1b" not in err


def test_provider_stages_are_flushed_before_http_request():
    from pathlib import Path

    import httpx

    from invoice_agent.console import ConsoleReporter
    from invoice_agent.llm import XAIClient
    from invoice_agent.models import LLMRequest

    class Flushed(io.StringIO):
        snapshot = ""

        def flush(self):
            self.snapshot = self.getvalue()

    progress = Flushed()
    console = ConsoleReporter([Path("invoice.txt")], progress, trace=True)
    events = EventCollector("r", on_event=console.event)
    events.source_id = "source-0001"
    expected = ""

    def handler(request):
        assert f"invoice.txt: {expected}" in progress.snapshot
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    client = XAIClient("test", transport=httpx.MockTransport(handler), events=events)
    try:
        for phase, expected in [
            ("inventory", "Checking inventory"),
            ("vp_propose", "Checking payment approval"),
            ("vp_critique", "Checking the approval decision"),
            ("vp_revise", "Rechecking approval after an issue was found"),
        ]:
            client.complete(LLMRequest(phase=phase, messages=[]))
    finally:
        client.close()


def test_cli_failure_after_payment_points_to_partial_artifacts(tmp_path, monkeypatch, capsys):
    import main
    from tests.doubles import ScenarioLLM

    class LocalModel(ScenarioLLM):
        def __init__(self, *args, **kwargs):
            super().__init__()

        def close(self):
            pass

    original_run = main.run

    def failing_run(*args, **kwargs):
        original_run(*args, **kwargs)
        raise OSError("private storage details")

    monkeypatch.setenv("XAI_API_KEY", "test-only-placeholder")
    monkeypatch.setattr(main, "XAIClient", LocalModel)
    monkeypatch.setattr(main, "run", failing_run)
    path = invoice(tmp_path / "a.json", "INV-1", "2026-01-01")
    output = tmp_path / "runs"
    assert main.main(["--invoice_path", str(path), "--output_dir", str(output)]) == 1
    captured = capsys.readouterr()
    artifact = next(output.glob("*/results.jsonl"))
    assert json.loads(artifact.read_text())["payment"]["status"] == "paid"
    assert str(artifact.parent) in captured.err
    assert "Completed results and simulated payments are saved" in captured.err
    assert "Run stopped early" in captured.err
    assert "private storage details" not in captured.err
    assert "Run complete:" not in captured.out


def test_trace_is_durable_before_progress_callback(tmp_path):
    trace_path = tmp_path / "trace.jsonl"
    seen = []

    def progress(event):
        rows = [json.loads(line) for line in trace_path.read_text().splitlines()]
        assert rows[-1]["event"] == event.event
        seen.append(event.event)
        raise OSError("progress unavailable")

    with trace_path.open("w") as stream:
        events = EventCollector("r", on_event=progress, trace_stream=stream)
        events.record("ingestion", "ingest_started", secret="hide", prompt_tokens=42)
        events.record("batch", "ingestion_barrier")
    rows = [json.loads(line) for line in trace_path.read_text().splitlines()]
    assert seen == ["ingest_started"]
    assert [r["sequence"] for r in rows] == [1, 2, 3]
    assert rows[0]["payload"]["secret"] == "[REDACTED]"
    assert rows[0]["payload"]["prompt_tokens"] == 42


def test_trace_failure_after_commit_preserves_paid_outcome(tmp_path):
    from invoice_agent.config import Policy
    from invoice_agent.database import SQLitePaymentStore
    from invoice_agent.runner import RunDependencies, run
    from tests.doubles import ScenarioLLM

    class FailCommitTrace:
        def __init__(self, stream):
            self.stream = stream

        def write(self, value):
            if '"event":"payment_committed"' in value:
                raise OSError("disk failure after commit")
            return self.stream.write(value)

        def flush(self):
            self.stream.flush()

        def fileno(self):
            return self.stream.fileno()

    path = invoice(tmp_path / "a.json", "INV-1", "2026-01-01")
    with (tmp_path / "trace.jsonl").open("w") as stream:
        events = EventCollector("r", trace_stream=FailCommitTrace(stream))
        store = SQLitePaymentStore(tmp_path / "run.db", "r", events=events)
        try:
            result = run([path], RunDependencies(store, ScenarioLLM(), events, Policy()))
            assert store.find_paid(result.results[0].identity) is not None
        finally:
            store.close()
    assert result.results[0].payment.status == "paid"
    assert result.summary.new_payments == 1
    assert result.summary.error.code == "STORAGE_ERROR"
    assert result.summary.metrics.run_error
    assert events.trace_failed
    assert any(e.event == "trace_output_unavailable" for e in events.events)


def test_console_summary_failure_does_not_overwrite_completed_metrics(
    tmp_path, monkeypatch, capsys
):
    import main
    from tests.doubles import ScenarioLLM

    class LocalModel(ScenarioLLM):
        def __init__(self, *args, **kwargs):
            super().__init__()

        def close(self):
            pass

    def broken_summary(*args):
        raise BrokenPipeError()

    monkeypatch.setenv("XAI_API_KEY", "test-only-placeholder")
    monkeypatch.setattr(main, "XAIClient", LocalModel)
    monkeypatch.setattr(main.ConsoleReporter, "summary", broken_summary)
    path = invoice(tmp_path / "a.json", "INV-1", "2026-01-01")
    output = tmp_path / "runs"
    assert main.main(["--invoice_path", str(path), "--output_dir", str(output)]) == 1
    directory = next(output.iterdir())
    metrics = json.loads((directory / "metrics.json").read_text())
    summary = json.loads((directory / "results.jsonl").read_text().splitlines()[-1])
    assert metrics["run_complete"] and metrics == summary["metrics"]
    assert "Processing completed" in capsys.readouterr().err


def test_real_client_zero_call_rejection_reports_zero_cost(tmp_path):
    import httpx

    from invoice_agent.config import Policy
    from invoice_agent.database import SQLitePaymentStore
    from invoice_agent.llm import XAIClient
    from invoice_agent.runner import RunDependencies, run

    def unexpected_request(request):
        raise AssertionError("Deterministic invalid date should not need a provider call")

    events = EventCollector("r")
    client = XAIClient("test", events=events, transport=httpx.MockTransport(unexpected_request))
    path = invoice(tmp_path / "invalid-date.json", "INV-1", "invalid-date")
    store = SQLitePaymentStore(tmp_path / "inventory.db", "r")
    try:
        result = run([path], RunDependencies(store, client, events, Policy()))
    finally:
        client.close()
        store.close()
    assert result.summary.rejected == 1
    assert result.summary.metrics.model_calls == 0
    assert result.summary.metrics.estimated_api_cost_usd == 0
    assert result.summary.metrics.cost_complete and result.summary.metrics.usage_complete
