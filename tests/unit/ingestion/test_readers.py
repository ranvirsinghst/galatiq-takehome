from decimal import Decimal
from pathlib import Path

import pytest
from pypdf import PdfWriter

from invoice_agent.config import Policy
from invoice_agent.ingestion import ingest
from invoice_agent.normalization import (
    normalize_date,
    normalize_decimal,
    normalize_invoice_number,
    normalize_item,
)
from invoice_agent.readers import SourceReadError, read_source

ROOT = Path(__file__).resolve().parents[3] / "data/invoices"


class NoLLM:
    def complete(self, request):
        raise AssertionError("Known structured layouts must not call the model")


class Events:
    run_id = "test"

    def __init__(self):
        self.events = []

    def emit(self, event):
        self.events.append(event)


@pytest.mark.parametrize(
    "name,count,total,vendor,due",
    [
        ("invoice_1006.csv", 2, "2750.00", "Acme Industrial Supplies", "2026-02-10"),
        ("invoice_1007.csv", 3, "15525.00", "MegaWidgets Corp", "2026-02-28"),
        ("invoice_1015.csv", 3, "6500.00", "Reliable Components Inc.", "2026-02-28"),
        ("invoice_1014.xml", 2, "4537.50", "TechParts International", "2026-02-26"),
    ],
)
def test_structured_fixtures(name, count, total, vendor, due):
    events = Events()
    result = ingest(read_source(ROOT / name, "s"), NoLLM(), Policy(), events)
    assert result.error is None and not result.rejected and result.attempts == 0
    c = result.candidate
    assert len(c.items) == count
    assert c.total_usd == Decimal(total)
    assert c.vendor_raw == vendor
    assert str(c.due_date) == due
    assert len(events.events) == 1
    if name.endswith("xml"):
        assert c.source_currency == "EUR"
        assert c.source_amounts["total"] == Decimal("4125.00")
        assert c.fx_rate_to_usd == Decimal("1.10")


def test_invalid_json_facts_preserved():
    c = ingest(read_source(ROOT / "invoice_1009.json", "s"), NoLLM(), Policy(), Events()).candidate
    assert c.vendor_raw == "" and c.due_date_raw is None and c.due_date is None
    assert c.items[0].quantity_raw == -5 and c.items[0].quantity == -5
    assert c.total_usd == Decimal("-250.00")


@pytest.mark.parametrize(
    "name,content",
    [
        ("a.json", '{"line_items":[],"date":"2026-01-01","date":"2026-01-02"}'),
        (
            "a.xml",
            '<!DOCTYPE invoice [<!ENTITY x SYSTEM "file:///etc/passwd">]><invoice>&x;</invoice>',
        ),
        ("a.csv", "field,value\nquantity,3\n"),
        ("a.json", '{"broken":'),
        ("a.xml", "<invoice>"),
        ("a.csv", "field,value\nitem,WidgetA,extra\n"),
    ],
)
def test_ambiguous_sources_reject(tmp_path, name, content):
    p = tmp_path / name
    p.write_text(content)
    with pytest.raises(SourceReadError) as exc:
        read_source(p, "s")
    assert exc.value.code == "SOURCE_PARSE_FAILED"


def test_bad_encoding_and_missing_file(tmp_path):
    p = tmp_path / "bad.txt"
    p.write_bytes(b"\xff")
    with pytest.raises(SourceReadError, match="Invalid txt"):
        read_source(p, "s")
    with pytest.raises(SourceReadError) as exc:
        read_source(tmp_path / "missing.txt", "s")
    assert exc.value.code == "SOURCE_IO_ERROR"


@pytest.mark.parametrize("kind", ["empty", "encrypted", "corrupt"])
def test_pdf_rejections(tmp_path, kind):
    p = tmp_path / "a.pdf"
    if kind == "corrupt":
        p.write_bytes(b"not a pdf")
    else:
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        if kind == "encrypted":
            writer.encrypt("secret")
        writer.write(p)
    with pytest.raises(SourceReadError):
        read_source(p, "s")


@pytest.mark.parametrize(
    "name,expected",
    [
        ("invoice_1011.pdf", "INV-1011"),
        ("invoice_1012.pdf", "INV 1012"),
        ("invoice_1013.pdf", "INV-1013"),
    ],
)
def test_actual_supplied_pdf_text(name, expected):
    source = read_source(ROOT / name, "s")
    assert expected in source.raw_text
    assert source.raw_data is None
    assert all(
        e.location.startswith("page:") and e.excerpt in source.raw_text for e in source.evidence
    )


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("3,500.O0", Decimal("3500.00")),
        (-5, Decimal("-5")),
        ("2.5", Decimal("2.5")),
        ("abc", None),
        (True, None),
        (None, None),
        ("NaN", None),
    ],
)
def test_decimal_normalization(raw, expected):
    assert normalize_decimal(raw) == expected


def test_conservative_normalization():
    assert str(normalize_date("26-Jan-2O26")) == "2026-01-26"
    assert normalize_date("yesterday") is None
    assert normalize_invoice_number("INV 1012") == "INV-1012"
    assert normalize_item("Widget A") == "WidgetA"
    assert normalize_item("WidgetA (rush order)") == "WidgetA"
    assert normalize_item("WidgetC") == "WidgetC"


def test_ocr_mapping_preserves_originals_and_qualifiers():
    from invoice_agent.normalization import candidate_from_mapping

    source = read_source(ROOT / "invoice_1012.txt", "s")
    c = candidate_from_mapping(
        {
            "invoice_number": "INV 1012",
            "vendor": "QuickShip Distributers (formerly FastShip Ltd.)",
            "date": "26-Jan-2O26",
            "total": "9,975.00",
            "line_items": [
                {"item": "Widget A", "quantity": "12", "unit_price": "250", "amount": "3,000.00"},
                {"item": "WidgetB", "quantity": "7", "unit_price": "500", "amount": "3,500.O0"},
                {"item": "Gadget X", "quantity": "4", "unit_price": "750", "amount": "3,000.00"},
            ],
        },
        source,
        Policy(),
    )
    assert c.invoice_number_raw == "INV 1012" and c.invoice_number_normalized == "INV-1012"
    assert c.invoice_date_raw == "26-Jan-2O26" and str(c.invoice_date) == "2026-01-26"
    assert c.items[1].source_tokens["amount"] == "3,500.O0"
    assert c.items[1].source_line_total == Decimal("3500.00")
    assert {
        "decimal token cleanup",
        "explicit date parsing/OCR year repair",
        "conservative inventory alias",
    } <= {n.method for n in c.normalizations}
    rush = candidate_from_mapping(
        {
            "date": "2026-01-27",
            "line_items": [{"item": "WidgetA (rush order)", "quantity": 4, "unit_price": 300}],
        },
        source,
        Policy(),
    ).items[0]
    assert (
        rush.description_raw == "WidgetA (rush order)"
        and rush.item_name_normalized == "WidgetA"
        and rush.unit_price_usd == 300
    )


@pytest.mark.parametrize("quantity", [True, "abc", None, "2.5", -5])
def test_invalid_tokens_survive_candidates(tmp_path, quantity):
    import json

    p = tmp_path / "a.json"
    p.write_text(
        json.dumps(
            {
                "date": "2026-01-01",
                "currency": "CAD",
                "line_items": [{"item": "WidgetA", "quantity": quantity, "unit_price": 250}],
                "total": 250,
            }
        )
    )
    result = ingest(read_source(p, "s"), NoLLM(), Policy(), Events())
    assert result.candidate.items[0].quantity_raw == quantity
    assert result.candidate.total_usd is None
    assert "UNSUPPORTED_CURRENCY" in {f.code for f in result.findings}
