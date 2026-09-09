import json
import sqlite3

from tests.doubles import ScenarioLLM
from tests.integration.test_batch import invoice


def test_interruption_preserves_committed_outcome_and_audit(tmp_path, monkeypatch, capsys):
    import main

    class InterruptSecond(ScenarioLLM):
        def __init__(self, *args, **kwargs):
            super().__init__()
            self.lookups = 0

        def complete(self, request):
            if request.phase == "inventory":
                self.lookups += 1
                if self.lookups == 2:
                    raise KeyboardInterrupt()
            return super().complete(request)

        def close(self):
            pass

    folder = tmp_path / "inputs"
    folder.mkdir()
    invoice(folder / "a.json", "INV-1", "2026-01-01", 2)
    invoice(folder / "b.json", "INV-2", "2026-01-02", 2)
    monkeypatch.setenv("XAI_API_KEY", "test-only-placeholder")
    monkeypatch.setattr(main, "XAIClient", InterruptSecond)
    output = tmp_path / "runs"
    assert main.main(["--invoice_path", str(folder), "--output_dir", str(output), "--json"]) == 130
    captured = capsys.readouterr()
    run_dir = next(output.iterdir())
    assert str(run_dir) in captured.err
    rows = [json.loads(line) for line in (run_dir / "results.jsonl").read_text().splitlines()]
    assert len(rows) == 1 and rows[0]["payment"]["status"] == "paid"
    audit = json.loads((run_dir / "audit.jsonl").read_text())
    assert audit["candidate"]["items"][0]["quantity"] == "2"
    assert audit["validation"]["candidate_digest"] == audit["review"]["candidate_digest"]
    assert len(audit["source_sha256"]) == 64
    assert audit["candidate"]["evidence"]
    assert audit["review"]["critique"]["verdict"] == "accept"
    with sqlite3.connect(run_dir / "inventory.db") as db:
        assert db.execute("SELECT count(*) FROM payments").fetchone()[0] == 1
    assert not any(json.loads(line)["type"] == "summary" for line in captured.out.splitlines())

    metrics = json.loads((run_dir / "metrics.json").read_text())
    assert not metrics["run_complete"] and metrics["run_error"]
    traces = [json.loads(line) for line in (run_dir / "trace.jsonl").read_text().splitlines()]
    assert traces and traces[-1]["source_id"] == "source-0002"
    assert "test-only-placeholder" not in (run_dir / "trace.jsonl").read_text()
