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
        assert f"{path.name}: Extracting" in progress.getvalue()
        return original(path, source_id)

    class LocalModel(ScenarioLLM):
        def __init__(self, *args, **kwargs):
            super().__init__()

        def complete(self, request):
            assert "a.json: Extracting" in progress.getvalue()
            assert "b.json: Extracting" in progress.getvalue()
            return super().complete(request)

        def close(self):
            pass

    monkeypatch.setattr(runner, "read_source", observing_reader)
    monkeypatch.setattr(main, "XAIClient", LocalModel)
    assert main.main(["--invoice_path", str(folder), "--output_dir", str(tmp_path / "runs")]) == 0
    assert "PAID" in out.getvalue()
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
    console = ConsoleReporter([Path("invoice.txt")], progress)
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
            ("vp_propose", "VP reviewing invoice"),
            ("vp_critique", "Checking VP decision"),
            ("vp_revise", "VP revising decision"),
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
    assert "partial results and any committed payments" in captured.err
    assert "No complete success summary emitted" in captured.err
    assert "private storage details" not in captured.err
    assert "Run complete:" not in captured.out
