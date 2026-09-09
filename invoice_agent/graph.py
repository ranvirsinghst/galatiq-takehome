"""Sequential processing graph composed from tool validation, VP review and payment."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from .approval import review
from .config import Policy
from .errors import AgentError
from .identity import payment_fingerprint, payment_identity
from .models import (
    Decision,
    ErrorInfo,
    ExecutionStatus,
    FindingOrigin,
    InvoiceCandidate,
    InvoiceResult,
    PaymentOutcome,
    PaymentStatus,
    ReviewOutcome,
    Severity,
    ValidationFinding,
    ValidationReport,
)
from .output import EventCollector
from .payment import build_payment_request
from .ports import LLMClient, MockPayment, PaymentStore
from .tools import validate_with_tools
from .validation import validate_source


class ProcessingState(TypedDict, total=False):
    result: InvoiceResult
    report: ValidationReport
    reviewed: ReviewOutcome


def process_invoice(
    candidate: InvoiceCandidate,
    source_path: str,
    index: int,
    store: PaymentStore,
    llm: LLMClient,
    policy: Policy,
    events: EventCollector,
    mock: MockPayment,
) -> InvoiceResult:
    common: dict[str, Any] = dict(
        run_id=events.run_id,
        source_id=candidate.source_id,
        source_path=source_path,
        processing_index=index,
        identity=payment_identity(candidate),
        invoice_date=candidate.invoice_date,
        total_usd=candidate.total_usd,
        source_currency=candidate.source_currency,
        fx_rate_to_usd=candidate.fx_rate_to_usd,
    )

    def rejected(
        reasons: list[str], findings: list[ValidationFinding] | None = None, **extra: Any
    ) -> InvoiceResult:
        return InvoiceResult(
            **common, decision=Decision.REJECTED, reasons=reasons, findings=findings or [], **extra
        )

    def error(info: ErrorInfo, **extra: Any) -> InvoiceResult:
        return InvoiceResult(
            **common, status=ExecutionStatus.ERROR, error=info, reasons=[info.message], **extra
        )

    def identity_check(state: ProcessingState) -> dict[str, Any]:
        identity = common["identity"]
        fingerprint = payment_fingerprint(candidate)
        if identity is None or fingerprint is None:
            return {}
        paid = store.find_paid(identity)
        if paid is None:
            return {}
        if paid.fingerprint == fingerprint:
            source_findings = validate_source(candidate, policy)
            blockers = [
                finding for finding in source_findings if finding.severity == Severity.BLOCKER
            ]
            if blockers:
                return {
                    "result": rejected([finding.message for finding in blockers], source_findings)
                }
            events.record("identity", "duplicate_skipped", payment_id=paid.payment_id)
            return {
                "result": InvoiceResult(
                    **common,
                    decision=Decision.APPROVED,
                    reasons=["Equivalent invoice already paid in this invocation."],
                    findings=source_findings,
                    payment=PaymentOutcome(
                        status=PaymentStatus.ALREADY_PAID,
                        payment_id=paid.payment_id,
                        amount_usd=paid.amount_usd,
                    ),
                )
            }
        finding = ValidationFinding(
            code="VERSION_CONFLICT",
            severity=Severity.BLOCKER,
            message="Changed version of an invoice already paid in this invocation.",
            origin=FindingOrigin.IDENTITY,
        )
        return {"result": rejected([finding.message], [finding])}

    def validation(state: ProcessingState) -> dict[str, Any]:
        outcome = validate_with_tools(candidate, store, llm, policy, events)
        if outcome.error:
            return {"result": error(outcome.error, attempts={"tool_rounds": outcome.tool_rounds})}
        assert outcome.report is not None
        return {"report": outcome.report}

    def vp(state: ProcessingState) -> dict[str, Any]:
        report = state["report"]
        outcome = review(candidate, report, llm, policy, events)
        attempts = {"vp_calls": outcome.semantic_calls, "vp_revisions": outcome.revision_count}
        if outcome.error:
            return {
                "reviewed": outcome,
                "result": error(outcome.error, findings=report.findings, attempts=attempts),
            }
        if not outcome.accepted:
            finding = ValidationFinding(
                code="REVIEW_EXHAUSTED",
                severity=Severity.BLOCKER,
                message="; ".join(outcome.rejection_reasons) or "VP review remained unresolved.",
            )
            return {
                "reviewed": outcome,
                "result": rejected(
                    [finding.message], [*report.findings, finding], attempts=attempts
                ),
            }
        if outcome.proposal is None or outcome.proposal.decision == Decision.REJECTED:
            reasons = (
                [outcome.proposal.reason_summary] if outcome.proposal else ["VP rejected invoice."]
            )
            return {
                "reviewed": outcome,
                "result": rejected(reasons, report.findings, attempts=attempts),
            }
        return {"reviewed": outcome}

    def payment(state: ProcessingState) -> dict[str, Any]:
        report, reviewed = state["report"], state["reviewed"]
        try:
            request = build_payment_request(candidate, report, reviewed, events.run_id)
        except (ValueError, ValidationError):
            return {
                "result": rejected(
                    ["Final payment gate rejected inconsistent or incomplete approval evidence."],
                    report.findings,
                )
            }
        outcome = store.pay(request, mock)
        if outcome.error:
            return {
                "result": error(
                    outcome.error, findings=[*report.findings, *outcome.findings], payment=outcome
                )
            }
        if outcome.status not in (PaymentStatus.PAID, PaymentStatus.ALREADY_PAID):
            return {
                "result": rejected(
                    [f.message for f in outcome.findings] or ["Payment gate rejected invoice."],
                    [*report.findings, *outcome.findings],
                    payment=outcome,
                )
            }
        return {
            "result": InvoiceResult(
                **common,
                decision=Decision.APPROVED,
                reasons=[reviewed.proposal.reason_summary if reviewed.proposal else "Approved"],
                findings=report.findings,
                payment=outcome,
                attempts={
                    "vp_calls": reviewed.semantic_calls,
                    "vp_revisions": reviewed.revision_count,
                },
            )
        }

    def timed(stage: str, node: Callable[[ProcessingState], dict[str, Any]]):
        def invoke(state: ProcessingState) -> dict[str, Any]:
            start = time.monotonic()
            try:
                return node(state)
            finally:
                events.record(
                    stage, "agent_stage_finished", elapsed_ms=(time.monotonic() - start) * 1000
                )

        return invoke

    graph = StateGraph(ProcessingState)
    for name, node in (
        ("identity", identity_check),
        ("validate", timed("validation", validation)),
        ("review", timed("approval", vp)),
        ("pay", timed("payment", payment)),
    ):
        graph.add_node(name, node)
    graph.add_edge(START, "identity")
    graph.add_conditional_edges("identity", lambda s: END if s.get("result") else "validate")
    graph.add_conditional_edges("validate", lambda s: END if s.get("result") else "review")
    graph.add_conditional_edges("review", lambda s: END if s.get("result") else "pay")
    graph.add_edge("pay", END)
    try:
        final = graph.compile().invoke({})
        return final["result"].model_copy(
            update={
                "candidate": candidate,
                "validation": final.get("report"),
                "review": final.get("reviewed"),
            }
        )
    except AgentError as exc:
        return error(exc.info)
