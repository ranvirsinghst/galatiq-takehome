from pathlib import Path

from invoice_agent.config import Policy
from invoice_agent.ingestion import ingest
from invoice_agent.models import LLMResponse
from invoice_agent.readers import read_source

ROOT = Path(__file__).resolve().parents[2] / "data/invoices"


class Events:
    run_id = "test"

    def __init__(self):
        self.events = []

    def emit(self, event):
        self.events.append(event)


class Scripted:
    def __init__(self, values):
        self.values = iter(values)
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        assert request.tools == []
        return LLMResponse(content=next(self.values))


def extraction_1002():
    return {
        "field_evidence": {
            "invoice_number": ["s:e2"],
            "vendor": ["s:e1"],
            "date": ["s:e3"],
            "due_date": ["s:e4"],
            "total": ["s:e7"],
        },
        "invoice_number": "1002",
        "vendor": "Gadgets Co.",
        "date": "Jan 30 2026",
        "due_date": "2026-01-30",
        "payment_terms": "Net 30",
        "total": "15,000.00",
        "line_items": [
            {"item": "GadgetX", "quantity": "20", "unit_price": "750", "evidence_refs": ["s:e6"]}
        ],
    }


def test_real_graph_repairs_omitted_date_once():
    source = read_source(ROOT / "invoice_1002.txt", "s")
    good = extraction_1002()
    bad = {**good, "date": None}
    model = Scripted([bad, good])
    events = Events()
    result = ingest(source, model, Policy(), events)
    assert result.attempts == 2 and not result.rejected and result.error is None
    assert str(result.candidate.invoice_date) == "2026-01-30"
    assert result.candidate.items[0].quantity == 20 and result.candidate.net_days == 30
    assert result.candidate.due_date == result.candidate.invoice_date
    assert "date must preserve" in model.requests[1].messages[-1]["content"]
    assert events.events[-1].event == "ingestion_complete"


def test_real_graph_exhaustion_has_no_candidate():
    model = Scripted([{**extraction_1002(), "date": None}] * 3)
    result = ingest(read_source(ROOT / "invoice_1002.txt", "s"), model, Policy(), Events())
    assert result.rejected and result.candidate is None and result.attempts == 3
    assert result.findings[0].code == "EXTRACTION_EXHAUSTED"


def test_schema_exhaustion_is_operational():
    model = Scripted([{"approve": True}] * 3)
    result = ingest(read_source(ROOT / "invoice_1002.txt", "s"), model, Policy(), Events())
    assert result.error.code == "LLM_SCHEMA_ERROR" and not result.rejected
    assert result.attempts == 3


def test_quantity_misread_is_repaired():
    good = extraction_1002()
    bad = {**good, "line_items": [{"item": "GadgetX", "quantity": 30, "unit_price": 750}]}
    result = ingest(
        read_source(ROOT / "invoice_1002.txt", "s"), Scripted([bad, good]), Policy(), Events()
    )
    assert result.attempts == 2 and result.candidate.items[0].quantity == 20


def test_missing_source_date_no_guess_or_retry(tmp_path):
    p = tmp_path / "a.txt"
    p.write_text("Vendor: Example\nTotal: $5.00\n")
    model = Scripted(
        [
            {
                "vendor": "Example",
                "total": "5",
                "field_evidence": {"vendor": ["s:e0"], "total": ["s:e1"]},
            }
        ]
    )
    result = ingest(read_source(p, "s"), model, Policy(), Events())
    assert result.rejected and result.attempts == 1
    assert result.findings[-1].code == "INVALID_DATE"


def test_actual_pdf_ingested_through_graph():
    source = read_source(ROOT / "invoice_1011.pdf", "s")
    data = {
        "invoice_number": "INV-1011",
        "vendor": "Summit Manufacturing Co.",
        "date": "2026-01-20",
        "due_date": "2026-02-20",
        "total": "3000.00",
        "field_evidence": {
            "invoice_number": ["s:e1"],
            "vendor": ["s:e2"],
            "date": ["s:e3"],
            "due_date": ["s:e4"],
            "total": ["s:e8"],
        },
        "line_items": [
            {
                "item": "WidgetA",
                "quantity": 6,
                "unit_price": "250.00",
                "amount": "1500.00",
                "evidence_refs": ["s:e6"],
            },
            {
                "item": "WidgetB",
                "quantity": 3,
                "unit_price": "500.00",
                "amount": "1500.00",
                "evidence_refs": ["s:e7"],
            },
        ],
    }
    result = ingest(source, Scripted([data]), Policy(), Events())
    assert result.error is None and not result.rejected
    assert result.candidate.total_usd == 3000
    assert len(result.candidate.items) == 2


def test_unknown_evidence_cannot_support_invented_date():
    good = extraction_1002()
    good["field_evidence"]["date"] = ["invented"]
    result = ingest(
        read_source(ROOT / "invoice_1002.txt", "s"), Scripted([good] * 3), Policy(), Events()
    )
    assert result.rejected and result.findings[0].code == "EXTRACTION_EXHAUSTED"


def test_all_reviewed_text_and_pdf_extractions():
    import json

    manifest = json.loads(
        (ROOT.parents[1] / "tests/fixtures/expected/extractions.json").read_text()
    )
    for name, data in manifest.items():
        data = json.loads(json.dumps(data).replace("{source_id}", "s"))
        model = Scripted([data])
        result = ingest(read_source(ROOT / name, "s"), model, Policy(), Events())
        assert result.error is None and not result.rejected, (name, result)
        assert result.attempts == 1
        assert result.candidate.invoice_number_normalized
