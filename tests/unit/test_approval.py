from datetime import date
from decimal import Decimal

import pytest

from invoice_agent.approval import review
from invoice_agent.config import Policy
from invoice_agent.models import (
    ErrorCode,
    InventorySnapshot,
    InvoiceCandidate,
    InvoiceLine,
    LLMResponse,
    ValidationFinding,
    ValidationReport,
    candidate_digest,
)


class Script:
    def __init__(self, values):
        self.values = iter(values)
        self.phases = []

    def complete(self, request):
        self.phases.append(request.phase)
        value = next(self.values)
        if isinstance(value, Exception):
            raise value
        return LLMResponse(content=value)


class Events:
    def __init__(self):
        self.events = []

    def emit(self, event):
        self.events.append(event)


def setup(total="100.00", findings=None):
    c = InvoiceCandidate(
        source_id="s",
        invoice_number_normalized="INV-1",
        vendor_normalized="V",
        invoice_date=date(2026, 1, 1),
        due_date=date(2026, 2, 1),
        total_usd=Decimal(total),
        items=[
            InvoiceLine(
                line_id="1", item_name_normalized="WidgetA", quantity_raw="1", quantity=Decimal(1)
            )
        ],
    )
    r = ValidationReport(
        candidate_digest=candidate_digest(c),
        stock_snapshot=InventorySnapshot(run_id="r", stock={"WidgetA": 15}),
        aggregate_quantities={"WidgetA": 1},
        complete=True,
        findings=findings or [],
        requires_high_value_review=c.total_usd > Decimal("10000"),
    )
    return c, r


def proposal(decision="approved", **kwargs):
    return {"decision": decision, "reason_summary": "Evidence supports this decision.", **kwargs}


ACCEPT = {"verdict": "accept"}
REVISE = {"verdict": "revise", "issues": ["Reconsider evidence"]}


def test_clean_two_calls_and_real_graph_events():
    c, r = setup()
    llm, events = Script([proposal(), ACCEPT]), Events()
    result = review(c, r, llm, Policy(), events)
    assert result.accepted
    assert result.semantic_calls == 2
    assert llm.phases == ["vp_propose", "vp_critique"]
    assert [e.event for e in events.events] == llm.phases


def test_endless_revision_bounded_six_calls():
    c, r = setup()
    llm = Script([proposal(), REVISE] * 3)
    result = review(c, r, llm, Policy(), Events())
    assert not result.accepted
    assert result.revision_count == 2
    assert result.semantic_calls == 6
    assert result.rejection_reasons[0] == "REVIEW_EXHAUSTED"


@pytest.mark.parametrize(
    "values,count", [([{}, proposal(), ACCEPT], 3), ([proposal(), {}, proposal(), ACCEPT], 4)]
)
def test_schema_failure_consumes_cycle_then_recovers(values, count):
    c, r = setup()
    result = review(c, r, Script(values), Policy(), Events())
    assert result.accepted and result.revision_count == 1
    assert result.semantic_calls == count


def test_final_schema_failure_is_operational():
    c, r = setup()
    result = review(c, r, Script([{}, {}, {}]), Policy(), Events())
    assert result.error.code == ErrorCode.LLM_SCHEMA_ERROR
    assert not result.accepted and result.semantic_calls == 3


def test_critique_cannot_override_blocker():
    findings = [
        ValidationFinding(code="INSUFFICIENT_STOCK", severity="blocker", message="Too many")
    ]
    c, r = setup(findings=findings)
    result = review(
        c,
        r,
        Script(
            [proposal(), ACCEPT, proposal("rejected", finding_codes=["INSUFFICIENT_STOCK"]), ACCEPT]
        ),
        Policy(),
        Events(),
    )
    assert result.accepted and result.proposal.decision == "rejected"
    assert result.revision_count == 1


@pytest.mark.parametrize("amount,accepted", [("10000.00", True), ("10000.01", False)])
def test_strict_high_value_boundary(amount, accepted):
    c, r = setup(amount)
    result = review(c, r, Script([proposal(), ACCEPT] * 3), Policy(), Events())
    assert result.accepted is accepted


def test_high_value_can_approve_with_complete_checklist():
    c, r = setup("10000.01")
    checks = {
        key: "Verified against invoice and report"
        for key in ["arithmetic", "aggregate_stock", "data_completeness", "suspicious_signals"]
    }
    result = review(
        c, r, Script([proposal(checks=checks, high_value_review=True), ACCEPT]), Policy(), Events()
    )
    assert result.accepted


def test_high_value_rejection_requires_checklist_and_prompt_explains_it():
    c, r = setup(
        "12000.00",
        findings=[
            ValidationFinding(code="INSUFFICIENT_STOCK", severity="blocker", message="Too many")
        ],
    )
    incomplete = proposal("rejected", finding_codes=["INSUFFICIENT_STOCK"])
    complete = {
        **incomplete,
        "high_value_review": True,
        "checks": {
            "arithmetic": "No arithmetic findings in report.",
            "aggregate_stock": "Report records insufficient stock; reject payment.",
            "data_completeness": "Validation is complete.",
            "suspicious_signals": "No additional suspicious findings recorded.",
        },
    }

    class PromptAwareScript(Script):
        def complete(self, request):
            assert "whether approved or rejected" in request.messages[0]["content"]
            return super().complete(request)

    llm = PromptAwareScript([incomplete, ACCEPT, complete, ACCEPT])
    outcome = review(c, r, llm, Policy(), Events())
    assert outcome.accepted
    assert outcome.proposal.decision == "rejected"
    assert outcome.revision_count == 1
    assert outcome.semantic_calls == 4


def test_warnings_require_acknowledgment():
    c, r = setup(
        findings=[
            ValidationFinding(
                code="TERMS_DATE_MISMATCH", severity="warning", message="Date mismatch"
            )
        ]
    )
    result = review(c, r, Script([proposal(), ACCEPT] * 3), Policy(), Events())
    assert not result.accepted


def test_unknown_evidence_and_finding_references_cannot_accept():
    c, r = setup()
    invalid = proposal(finding_codes=["INVENTED_CHECK"], evidence_refs=["nonexistent"])
    outcome = review(c, r, Script([invalid, ACCEPT] * 3), Policy(), Events())
    assert not outcome.accepted
    assert outcome.semantic_calls == 6


def test_unavailable_arithmetic_must_be_acknowledged():
    c, r = setup()
    r = r.model_copy(update={"unavailable_checks": ["line:1"]})
    outcome = review(c, r, Script([proposal(), ACCEPT] * 3), Policy(), Events())
    assert not outcome.accepted


def test_candidate_mutation_before_review_requires_no_model_calls():
    c, r = setup()
    c.assumptions.append("Changed after validation")
    llm = Script([])
    outcome = review(c, r, llm, Policy(), Events())
    assert not outcome.accepted
    assert outcome.semantic_calls == 0
    assert "LATE_EXTRACTION_ERROR" in outcome.rejection_reasons[0]


@pytest.mark.parametrize(
    "critique",
    [
        {"verdict": "accept", "issues": ["Unsupported claim remains"]},
        {"verdict": "accept", "required_changes": ["Recheck total"]},
    ],
)
def test_accept_with_unresolved_issues_never_authorizes(critique):
    c, r = setup()
    result = review(c, r, Script([proposal(), critique] * 3), Policy(), Events())
    assert not result.accepted and result.semantic_calls == 6
    assert result.rejection_reasons[0] == "REVIEW_EXHAUSTED"
