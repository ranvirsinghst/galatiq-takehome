"""Source-backed regressions from final independent review."""

import json
from decimal import Decimal

import pytest

from invoice_agent.config import Policy
from invoice_agent.ingestion import _source_issues, ingest
from invoice_agent.models import Evidence, SourceDocument
from invoice_agent.money import convert_to_usd
from invoice_agent.normalization import normalize_decimal
from invoice_agent.ports import NullEventSink
from invoice_agent.readers import SourceReadError, read_source


class NoLLM:
    def complete(self, request):
        raise AssertionError("Known structured source must not call LLM")


def text_source(text):
    return SourceDocument(
        source_id="s",
        path="test.txt",
        format="txt",
        content_sha256="h",
        raw_text=text,
        evidence=[Evidence(source_id="s", evidence_id="e", location="line:1", excerpt=text)],
    )


def test_quantity_keyword_does_not_allow_numeric_substrings():
    source = text_source("WidgetA quantity 12 unit price 10 amount 120")
    data = {
        "line_items": [
            {
                "item": "WidgetA",
                "quantity": "2",
                "unit_price": "10",
                "amount": "120",
                "evidence_refs": ["e"],
            }
        ]
    }
    assert _source_issues(data, source)
    data["line_items"][0]["quantity"] = "12"
    assert _source_issues(data, source) == []


@pytest.mark.parametrize("field", ["unit_price", "amount"])
def test_available_labeled_line_amount_cannot_disappear(field):
    source = text_source("WidgetA quantity 12 unit price 10 amount 120")
    line = {
        "item": "WidgetA",
        "quantity": "12",
        "unit_price": "10",
        "amount": "120",
        "evidence_refs": ["e"],
    }
    line.pop(field)
    assert _source_issues({"line_items": [line]}, source)


@pytest.mark.parametrize("field", ["unit_price", "amount"])
def test_available_table_amount_cannot_disappear(field):
    source = text_source("WidgetA 12 10.00 120.00")
    line = {
        "item": "WidgetA",
        "quantity": 12,
        "unit_price": "10.00",
        "amount": "120.00",
        "evidence_refs": ["e"],
    }
    line.pop(field)
    assert _source_issues({"line_items": [line]}, source)


@pytest.mark.parametrize("label", ["Subtotal", "Tax (5%)", "Shipping", "Total Amount"])
def test_available_footer_amount_cannot_disappear(label):
    assert _source_issues({}, text_source(f"{label}: $12.00"))


@pytest.mark.parametrize("value", ["1,23", "€1.234,56", "12,34,567", "1,234.5,6"])
def test_ambiguous_locale_is_not_reinterpreted(value):
    assert normalize_decimal(value) is None


def test_supported_grouping_and_ocr_remain_valid():
    assert normalize_decimal("$1,234.56") == Decimal("1234.56")
    assert normalize_decimal("3,500.O0") == Decimal("3500.00")


@pytest.mark.parametrize("entries", ["tax,5\ntax,6", "tax_amount,5\ntax,6", "tax,5\ntax_amount,6"])
def test_csv_tax_alias_duplicate_checked_before_mapping(tmp_path, entries):
    path = tmp_path / "a.csv"
    path.write_text("field,value\n" + entries + "\n")
    with pytest.raises(SourceReadError, match="Conflicting CSV"):
        read_source(path, "s")


@pytest.mark.parametrize("tag", ["header", "totals", "payment_terms", "line_items"])
def test_repeated_xml_containers_reject(tmp_path, tag):
    path = tmp_path / "a.xml"
    containers = "<header><date>2026-01-01</date></header>" if tag != "header" else ""
    path.write_text(f"<invoice>{containers}<{tag}/><{tag}/></invoice>")
    with pytest.raises(SourceReadError, match="Repeated XML"):
        read_source(path, "s")


def test_unfamiliar_nested_json_uses_interpretation_fallback(tmp_path):
    path = tmp_path / "a.json"
    path.write_text(
        json.dumps(
            {
                "vendor": {"name": "Supplier"},
                "document": {"issued": "2026-01-01", "id": "INV-1", "products": []},
            }
        )
    )
    assert read_source(path, "s").raw_data is None


def test_missing_known_flat_json_fields_not_invented(tmp_path):
    path = tmp_path / "a.json"
    path.write_text('{"vendor":"Supplier"}')
    result = ingest(read_source(path, "s"), NoLLM(), Policy(), NullEventSink())
    assert result.rejected and result.attempts == 0
    assert result.candidate.vendor_raw == "Supplier"


@pytest.mark.parametrize("amount,currency", [("1e100", "USD"), ("9.9e23", "EUR")])
def test_large_finite_source_amount_is_business_finding(tmp_path, amount, currency):
    path = tmp_path / "a.json"
    path.write_text(
        json.dumps(
            {
                "invoice_number": "INV-1",
                "vendor": "Supplier",
                "date": "2026-01-01",
                "currency": currency,
                "total": amount,
                "line_items": [],
            }
        )
    )
    result = ingest(read_source(path, "s"), NoLLM(), Policy(), NullEventSink())
    assert result.error is None
    assert result.candidate.total_usd is None
    assert result.candidate.source_tokens["total"] == amount
    assert "INVALID_AMOUNT" in {f.code for f in result.findings}


def test_money_precision_error_is_controlled():
    with pytest.raises(ValueError, match="magnitude"):
        convert_to_usd(Decimal("1e100"), Decimal(1))


def test_invalid_source_quantity_stays_invalid_not_repaired():
    data = {"line_items": [{"item": "WidgetA", "quantity": "abc", "evidence_refs": ["e"]}]}
    assert _source_issues(data, text_source("WidgetA quantity abc")) == []


def test_duplicate_xml_totals_reject_even_without_header(tmp_path):
    path = tmp_path / "a.xml"
    path.write_text("<invoice><totals/><totals/></invoice>")
    with pytest.raises(SourceReadError, match="Repeated XML"):
        read_source(path, "s")
