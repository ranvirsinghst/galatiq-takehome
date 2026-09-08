from decimal import Decimal
from pathlib import Path

import pytest

from invoice_agent.config import Policy
from invoice_agent.normalization import candidate_from_mapping, normalize_tax_rate
from invoice_agent.readers import read_source
from invoice_agent.validation import validate_source


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("0%", "0"),
        ("5%", ".05"),
        (" 7.5 % ", ".075"),
        ("1O%", ".10"),
        ("0.05", ".05"),
        (Decimal(".075"), ".075"),
        (0, "0"),
    ],
)
def test_rate_forms(raw, expected):
    assert normalize_tax_rate(raw) == Decimal(expected)


@pytest.mark.parametrize(
    "raw", ["%", "5%%", "%5", "5% extra", "NaN%", "1,5%", "USD 5%", True, None, "1e999%"]
)
def test_malformed_rates(raw):
    assert normalize_tax_rate(raw) is None


@pytest.mark.parametrize(
    "raw,expected", [("0%", Decimal(0)), ("7.5%", Decimal(".075")), ("broken%", None)]
)
def test_source_rate_provenance_and_validation(raw, expected):
    source = read_source(Path("data/invoices/invoice_1001.txt"), "tax-source")
    refs = [source.evidence[0].evidence_id]
    candidate = candidate_from_mapping(
        {
            "tax_rate": raw,
            "subtotal": "100",
            "tax_amount": "0" if expected == 0 else "7.50",
            "field_evidence": {"tax_rate": refs},
        },
        source,
        Policy(),
    )
    assert candidate.source_amounts["tax_rate"] == expected
    assert candidate.source_tokens["tax_rate"] == raw
    findings = validate_source(candidate, Policy())
    assert any(f.code == "INVALID_AMOUNT" and f.field == "tax_rate" for f in findings) == (
        expected is None
    )
    if expected is not None:
        note = next(n for n in candidate.normalizations if n.field == "tax_rate")
        assert note.original_value == raw
        assert Decimal(note.normalized_value) == expected
        assert note.evidence_ids == refs
        assert note.method == "explicit percentage to decimal rate"
        assert not any(f.code == "TOTAL_MISMATCH" and f.field == "tax_amount" for f in findings)
