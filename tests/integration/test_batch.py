import json
from pathlib import Path

from invoice_agent.config import Policy
from invoice_agent.database import SQLitePaymentStore
from invoice_agent.output import EventCollector
from invoice_agent.runner import RunDependencies, discover, run
from tests.doubles import ScenarioLLM


def invoice(path, number, issue, quantity=2, total=None):
    total = quantity * 10 if total is None else total
    path.write_text(
        json.dumps(
            {
                "invoice_number": number,
                "vendor": {"name": "Test Vendor"},
                "date": issue,
                "due_date": "2026-12-31",
                "currency": "USD",
                "line_items": [{"item": "WidgetA", "quantity": quantity, "unit_price": 10}],
                "subtotal": total,
                "tax_amount": 0,
                "total": total,
            }
        )
    )
    return path


def execute(paths, tmp_path, name="run", llm=None):
    events = EventCollector(name)
    store = SQLitePaymentStore(tmp_path / f"{name}.db", name, events=events)
    try:
        result = run(paths, RunDependencies(store, llm or ScenarioLLM(), events, Policy()))
        return result
    finally:
        store.close()


def test_date_order_barrier_and_stateful_depletion(tmp_path):
    later = invoice(tmp_path / "a.json", "INV-2", "2026-02-01", 8)
    earlier = invoice(tmp_path / "z.json", "INV-1", "2026-01-01", 10)
    result = execute([later, earlier], tmp_path)
    assert [r.identity.invoice_number for r in result.results] == ["INV-1", "INV-2"]
    assert [r.decision for r in result.results] == ["approved", "rejected"]
    assert result.summary.final_inventory["WidgetA"] == 5
    terminal = [e.sequence for e in result.trace if e.event == "ingest_terminal"]
    processing = [
        e.sequence for e in result.trace if e.stage in ("processing", "validation", "vp", "payment")
    ]
    assert len(terminal) == 2 and max(terminal) < min(processing)


def test_ties_duplicates_versions_and_fresh_runs(tmp_path):
    original = invoice(tmp_path / "a.json", "INV-1", "2026-01-01", 10)
    duplicate = invoice(tmp_path / "b.json", "INV-1", "2026-01-01", 10)
    revised = invoice(tmp_path / "c.json", "INV-1", "2026-01-01", 11)
    paths = [revised, duplicate, original]
    for name in ("one", "two"):
        result = execute(paths, tmp_path, name)
        assert [Path(r.source_path).name for r in result.results] == ["a.json", "b.json", "c.json"]
        assert [r.payment.status for r in result.results] == ["paid", "already_paid", "not_paid"]
        assert result.summary.new_payments == 1 and result.summary.duplicate_skips == 1
        assert result.summary.total_paid_usd == 100
        assert result.summary.final_inventory["WidgetA"] == 5
        assert result.results[-1].findings[0].code == "VERSION_CONFLICT"


def test_malformed_input_does_not_suppress_healthy_file(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{bad")
    good = invoice(tmp_path / "good.json", "INV-1", "2026-01-01")
    result = execute([bad, good], tmp_path)
    assert len(result.results) == 2
    assert result.summary.rejected == 1 and result.summary.new_payments == 1
    assert result.summary.operational_errors == 0


def test_deterministic_blocker_skips_hostile_vp(tmp_path):
    bad = invoice(tmp_path / "bad.json", "INV-1", "2026-01-01", 20)
    result = execute([bad], tmp_path, llm=ScenarioLLM(hostile=True))
    assert result.summary.new_payments == 0
    assert result.summary.final_inventory["WidgetA"] == 15
    assert result.results[0].decision == "rejected"
    assert any(f.code == "INSUFFICIENT_STOCK" for f in result.results[0].findings)
    assert result.results[0].review is None
    assert result.results[0].attempts["vp_calls"] == 0
    assert not any(e.event in {"vp_propose", "vp_critique", "vp_revise"} for e in result.trace)


def test_discovery_and_empty_directory(tmp_path):
    import pytest

    with pytest.raises(ValueError):
        discover(tmp_path)
    good = invoice(tmp_path / "a.json", "INV-1", "2026-01-01")
    skipped = tmp_path / "notes.md"
    skipped.write_text("not invoice")
    paths, ignored = discover(tmp_path)
    assert paths == [good] and ignored == [str(skipped)]
