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
    events = EventCollector("run", secrets=["xai-a-real-test-sentinel"])
    events.emit(
        TraceEvent(
            run_id="",
            stage="test",
            event="test",
            payload={"message": "contains xai-a-real-test-sentinel"},
        )
    )
    assert "xai-a-real-test-sentinel" not in events.events[0].model_dump_json()


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
        main.main(["--invoice_path", str(path), "--output_dir", str(tmp_path / "runs"), "--trace"])
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
    assert main.main(["--invoice_path", str(path), "--output_dir", str(tmp_path / "runs")]) == 1
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert rows[0]["payment"]["status"] == "paid"
    assert rows[-1]["operational_errors"] == 0
    assert rows[-1]["error"]["code"] == "STORAGE_ERROR"
    assert rows[-1]["final_inventory"] == {}
