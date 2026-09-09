"""Exercise report safety and accounting without a provider or browser."""

from decimal import Decimal
from html.parser import HTMLParser

import pytest

from invoice_agent.metrics import RunMetrics
from invoice_agent.models import (
    Critique,
    ErrorInfo,
    InvoiceIdentity,
    InvoiceResult,
    PaymentOutcome,
    Proposal,
    ReviewOutcome,
    RunResult,
    RunSummary,
    TraceEvent,
)
from invoice_agent.report import render_report, write_report


class Document(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.tags = []
        self.parts = []
        self.readable_parts = []
        self.stack = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))
        if tag not in {"meta", "link", "br", "hr", "input", "img"}:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in self.stack:
            self.stack = self.stack[: len(self.stack) - 1 - self.stack[::-1].index(tag)]

    def handle_data(self, data):
        self.parts.append(data)
        if not {"pre", "style", "script"} & set(self.stack):
            self.readable_parts.append(data)

    @property
    def text(self):
        return " ".join(self.parts)


def sample(*, metrics=None, trace=None):
    outcomes = []
    for source, status in (("new", "paid"), ("duplicate", "already_paid"), ("pending", "not_paid")):
        outcomes.append(
            InvoiceResult(
                run_id="run-123",
                source_id=source,
                source_path=f"{source}.json",
                identity=InvoiceIdentity(vendor="Acme", invoice_number=source),
                decision="approved",
                total_usd=Decimal("17.25"),
                reasons=["Review accepted"],
                payment=PaymentOutcome(
                    status=status,
                    payment_id="payment-123" if status != "not_paid" else None,
                    amount_usd=Decimal("17.25") if status != "not_paid" else None,
                ),
            )
        )
    return RunResult(
        results=outcomes,
        summary=RunSummary(
            run_id="run-123",
            discovered=3,
            completed=3,
            approved=3,
            new_payments=1,
            duplicate_skips=1,
            total_paid_usd=Decimal("17.25"),
            metrics=metrics,
        ),
        trace=trace or [],
    )


def test_report_escapes_untrusted_text_and_redacts_credentials():
    hostile = '<img src=x onerror="alert(1)"> & <script>alert(2)</script>'
    secret = "xai-report-secret-sentinel"
    result = sample()
    changed = result.results[0].model_copy(
        update={
            "source_path": hostile,
            "identity": InvoiceIdentity(vendor=hostile, invoice_number="INV-1"),
            "reasons": [f"{hostile} {secret}"],
        }
    )
    result = result.model_copy(
        update={
            "results": [changed],
            "trace": [
                TraceEvent(
                    run_id="run-123",
                    stage="vp",
                    event="vp_critique",
                    sequence=1,
                    payload={"issues": [f"{hostile} {secret}"]},
                )
            ],
        }
    )
    html = render_report(result, secrets=[secret])
    parsed = Document(html)
    assert hostile in parsed.text
    assert secret not in html
    assert "[REDACTED]" in parsed.text
    assert not any(tag == "img" for tag, _ in parsed.tags)
    assert not any(key.startswith("on") for _, attrs in parsed.tags for key in attrs)
    assert "<script>alert(2)</script>" not in html


def test_report_distinguishes_approval_duplicate_and_actual_payment():
    parsed = Document(render_report(sample()))
    text = parsed.text.lower()
    assert "approved" in text
    assert "already paid" in text or "already_paid" in text
    assert "not paid" in text or "not_paid" in text
    assert "$17.25" in text
    assert "payment-123" in text
    assert "new.json" in text and "duplicate.json" in text and "pending.json" in text


@pytest.mark.parametrize("metrics", [None, RunMetrics()])
def test_missing_accounting_is_explicitly_unavailable(metrics):
    text = Document(render_report(sample(metrics=metrics))).text.lower()
    assert "unavailable" in text or "unknown" in text
    assert "token" in text


def test_partial_cost_and_usage_are_labeled_as_incomplete():
    metrics = RunMetrics(
        model_calls=2,
        prompt_tokens=1250,
        completion_tokens=250,
        total_tokens=1500,
        estimated_api_cost_usd=Decimal("0.012345"),
        cost_basis="provider cost_in_usd_ticks",
        cost_complete=False,
        usage_complete=False,
    )
    text = Document(render_report(sample(metrics=metrics))).text.lower()
    assert "incomplete" in text or "partial" in text
    assert "lower bound" in text or "at least" in text or "known cost" in text
    assert "1,500" in text or "1500" in text
    assert "0.012345" in text


def test_expandable_process_preserves_observed_order_and_critique():
    trace = [
        TraceEvent(
            run_id="run-123",
            source_id="new",
            sequence=1,
            stage="vp",
            event="vp_propose",
            payload={"reason_summary": "first-proposal-marker"},
        ),
        TraceEvent(
            run_id="run-123",
            source_id="new",
            sequence=2,
            stage="vp",
            event="vp_critique",
            payload={"issues": ["second-critique-marker"]},
        ),
        TraceEvent(
            run_id="run-123",
            source_id="new",
            sequence=3,
            stage="vp",
            event="vp_revise",
            payload={"reason_summary": "third-revision-marker"},
        ),
    ]
    parsed = Document(render_report(sample(trace=trace)))
    assert sum(tag == "details" for tag, _ in parsed.tags) >= 3
    assert sum(tag == "summary" for tag, _ in parsed.tags) >= 3
    text = parsed.text
    assert text.index("first-proposal-marker") < text.index("second-critique-marker")
    assert text.index("second-critique-marker") < text.index("third-revision-marker")
    assert "critique" in text.lower()


def test_report_is_standalone_and_write_replaces_previous_artifact(tmp_path):
    path = tmp_path / "report.html"
    path.write_text("stale report")
    result = sample()
    write_report(result, path)
    html = path.read_text()
    assert "stale report" not in html
    assert html == render_report(result)
    parsed = Document(html)
    assert any(tag == "html" for tag, _ in parsed.tags)
    assert not any(tag == "script" and attrs.get("src") for tag, attrs in parsed.tags)
    assert not any(tag == "link" and attrs.get("rel") == "stylesheet" for tag, attrs in parsed.tags)


def test_failed_atomic_replace_preserves_existing_report(tmp_path, monkeypatch):
    import invoice_agent.report as report

    path = tmp_path / "report.html"
    path.write_text("previous complete report")

    def fail_replace(source, destination):
        assert destination == path
        assert "<!doctype html>" in source.read_text().lower()
        raise OSError("disk unavailable")

    monkeypatch.setattr(report.os, "replace", fail_replace)
    with pytest.raises(OSError, match="disk unavailable"):
        write_report(sample(), path)
    assert path.read_text() == "previous complete report"
    assert list(tmp_path.iterdir()) == [path]


def test_json_evidence_redacts_credentials_before_serialization():
    secret = 'sentinel"credential\\with\nnewlines'
    event = TraceEvent(
        run_id="run-123",
        stage="vp",
        event="vp_critique",
        payload={"issues": [secret]},
    )
    html = render_report(sample(trace=[event]), secrets=[secret])
    assert "sentinel" not in html
    assert "[REDACTED]" in html


def test_unfinished_trace_preserves_error_code_and_severity():
    event = TraceEvent(
        run_id="run-123",
        source_id="unfinished",
        stage="vp",
        event="response_error",
        severity="critical",
        error_code="INTERNAL_ERROR",
    )
    text = Document(render_report(sample(trace=[event]))).text
    assert "INTERNAL_ERROR" in text
    assert "critical" in text
    assert "Unfinished source" in text


def test_missing_metrics_does_not_claim_interruption_or_hide_known_inventory():
    result = sample()
    result = result.model_copy(
        update={"summary": result.summary.model_copy(update={"final_inventory": {"WidgetA": 9}})}
    )
    text = Document(render_report(result)).text
    assert "Run completion unavailable" in text
    assert "Partial run" not in text
    assert "WidgetA" in text


def with_review(review, trace=None):
    result = sample(trace=trace)
    item = result.results[0].model_copy(update={"review": review})
    return result.model_copy(update={"results": [item]})


def review_event(name, sequence, **payload):
    return TraceEvent(
        run_id="run-123",
        source_id="new",
        stage="vp",
        event=name,
        sequence=sequence,
        payload=payload,
    )


def test_vp_review_exposes_proposal_feedback_revision_and_final_rationale():
    final_proposal = Proposal(
        decision="approved",
        reason_summary="Approval includes the previously omitted date warning.",
        checks={"arithmetic": "The line total equals the invoice total."},
        finding_codes=["TERMS_DATE_MISMATCH"],
    )
    final_critique = Critique(
        verdict="accept", reason_summary="The revised proposal acknowledges the date warning."
    )
    outcome = ReviewOutcome(
        candidate_digest="sample",
        proposal=final_proposal,
        critique=final_critique,
        accepted=True,
        revision_count=1,
        semantic_calls=4,
    )
    trace = [
        review_event(
            "vp_propose",
            1,
            cycle=1,
            decision="approved",
            reason="Initial approval omitted the date warning.",
        ),
        review_event(
            "vp_critique",
            2,
            cycle=1,
            verdict="revise",
            accepted=False,
            reason="The proposal does not address the date discrepancy.",
            agent_issues=["Due date differs from payment terms."],
            required_changes=["Explain the date discrepancy."],
            deterministic_issues=["Warnings must be acknowledged."],
        ),
        review_event(
            "vp_revise",
            3,
            cycle=2,
            decision="approved",
            reason=final_proposal.reason_summary,
            checks=final_proposal.checks,
            finding_codes=final_proposal.finding_codes,
        ),
        review_event(
            "vp_critique",
            4,
            cycle=2,
            verdict="accept",
            accepted=True,
            reason=final_critique.reason_summary,
            agent_issues=[],
            required_changes=[],
            deterministic_issues=[],
        ),
    ]
    parsed = Document(render_report(with_review(outcome, trace)))
    readable = " ".join(parsed.readable_parts)
    for marker in [
        "Initial approval omitted the date warning.",
        "The proposal does not address the date discrepancy.",
        "Due date differs from payment terms.",
        "Explain the date discrepancy.",
        "Warnings must be acknowledged.",
        final_proposal.reason_summary,
        "The line total equals the invoice total.",
        final_critique.reason_summary,
    ]:
        assert marker in readable
    assert readable.index("Initial approval omitted") < readable.index(
        "The proposal does not address"
    )
    assert readable.index("The proposal does not address") < readable.index(
        final_proposal.reason_summary
    )
    assert any(
        tag == "details" and "open" in attrs and "vp-review" in attrs.get("class", "").split()
        for tag, attrs in parsed.tags
    )


def test_vp_review_without_trace_displays_persisted_agent_rationales():
    outcome = ReviewOutcome(
        candidate_digest="sample",
        accepted=True,
        proposal=Proposal(decision="approved", reason_summary="Recorded proposal explanation."),
        critique=Critique(verdict="accept", reason_summary="Recorded critique explanation."),
    )
    readable = " ".join(Document(render_report(with_review(outcome))).readable_parts)
    assert "Recorded proposal explanation." in readable
    assert "Recorded critique explanation." in readable


def test_vp_review_legacy_record_does_not_invent_critique_rationale():
    outcome = ReviewOutcome(
        candidate_digest="sample",
        accepted=True,
        proposal=Proposal(
            decision="approved", reason_summary="The recorded proposal supports approval."
        ),
        critique=Critique(verdict="accept"),
    )
    readable = " ".join(Document(render_report(with_review(outcome))).readable_parts).lower()
    assert "rationale" in readable or "explanation" in readable
    assert "not recorded" in readable or "unavailable" in readable


def test_accept_verdict_does_not_hide_unresolved_deterministic_safeguards():
    outcome = ReviewOutcome(
        candidate_digest="sample",
        accepted=False,
        proposal=Proposal(decision="approved", reason_summary="The agent recommended payment."),
        critique=Critique(verdict="accept", reason_summary="The agent accepted the proposal."),
        rejection_reasons=["REVIEW_EXHAUSTED", "Required stock assessment is missing."],
    )
    trace = [
        review_event(
            "vp_critique",
            1,
            verdict="accept",
            accepted=False,
            reason="The agent accepted the proposal.",
            agent_issues=[],
            required_changes=[],
            deterministic_issues=["Required stock assessment is missing."],
        )
    ]
    readable = " ".join(Document(render_report(with_review(outcome, trace))).readable_parts)
    assert "Unresolved — no accepted VP decision" in readable
    assert "Round result: not accepted." in readable
    assert "Required stock assessment is missing." in readable
    assert "Final VP decision:" not in readable
    assert "Round result: review accepted." not in readable


def test_vp_operational_failure_does_not_present_last_proposal_as_final_decision():
    outcome = ReviewOutcome(
        candidate_digest="sample",
        accepted=False,
        proposal=Proposal(decision="approved", reason_summary="Last proposal before the failure."),
        error=ErrorInfo(code="LLM_SCHEMA_ERROR", message="VP critique response was invalid."),
    )
    readable = " ".join(Document(render_report(with_review(outcome))).readable_parts)
    assert "Incomplete — VP review failed" in readable
    assert "VP critique response was invalid." in readable
    assert "Final VP decision:" not in readable


def test_deterministic_rejection_is_explicit_about_skipping_vp():
    trace = [
        review_event("review_rejected_by_rules", 1, vp_skipped=True, codes=["INSUFFICIENT_STOCK"])
    ]
    result = with_review(None, trace)
    item = result.results[0].model_copy(update={"decision": "rejected"})
    result = result.model_copy(update={"results": [item]})
    readable = " ".join(Document(render_report(result)).readable_parts)
    assert "Skipped — deterministic checks blocked payment" in readable
    assert "Final VP decision:" not in readable


def test_partial_trace_preserves_final_saved_explanations_outside_json():
    outcome = ReviewOutcome(
        candidate_digest="sample",
        accepted=True,
        proposal=Proposal(decision="approved", reason_summary="Final proposal retained in audit."),
        critique=Critique(verdict="accept", reason_summary="Final critique retained in audit."),
    )
    trace = [
        review_event("vp_propose", 1, cycle=1, decision="rejected", reason="Earlier proposal.")
    ]
    readable = " ".join(Document(render_report(with_review(outcome, trace))).readable_parts)
    assert "Final proposal retained in audit." in readable
    assert "Final critique retained in audit." in readable


def test_response_error_is_attributed_to_review_system():
    trace = [review_event("response_error", 17, reason="Provider response did not match schema.")]
    readable = " ".join(Document(render_report(sample(trace=trace))).readable_parts)
    assert "Review system · event #17" in readable
    assert "VP agent · event #17" not in readable


def test_source_corrections_and_normalizations_are_readable():
    from invoice_agent.models import InvoiceCandidate, InvoiceLine, Normalization

    candidate = InvoiceCandidate(
        source_id="new",
        assumptions=["Currency assumed USD."],
        normalizations=[
            Normalization(
                field="currency", original_value=None, normalized_value="USD", method="mock policy"
            )
        ],
        items=[
            InvoiceLine(
                line_id="line1",
                normalizations=[
                    Normalization(
                        field="quantity",
                        original_value="1O",
                        normalized_value="10",
                        method="OCR correction",
                    )
                ],
            )
        ],
    )
    result = sample(
        trace=[
            TraceEvent(
                run_id="run-123",
                source_id="new",
                stage="ingestion",
                event="extraction_repair_required",
                payload={"attempt": 1, "issues": ["Quantity needs source evidence."]},
            )
        ]
    )
    result = result.model_copy(
        update={"results": [result.results[0].model_copy(update={"candidate": candidate})]}
    )
    readable = " ".join(Document(render_report(result)).readable_parts)
    for value in [
        "Quantity needs source evidence.",
        "1O",
        "10",
        "OCR correction",
        "Currency assumed USD.",
    ]:
        assert value in readable
