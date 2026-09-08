"""Fresh-run lifecycle with an explicit all-ingestion barrier and chronological payments."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from .config import Policy
from .errors import AgentError
from .graph import process_invoice
from .ingestion import ingest
from .models import (
    Decision,
    ErrorCode,
    ErrorInfo,
    ExecutionStatus,
    FindingOrigin,
    IngestionOutcome,
    InvoiceResult,
    PaymentStatus,
    RunResult,
    RunSummary,
    Severity,
    ValidationFinding,
)
from .output import EventCollector
from .payment import mock_payment
from .ports import LLMClient, MockPayment, PaymentStore
from .readers import read_source

SUPPORTED = {".txt", ".json", ".csv", ".xml", ".pdf"}


@dataclass
class RunDependencies:
    store: PaymentStore
    llm: LLMClient
    events: EventCollector
    policy: Policy
    mock: MockPayment = mock_payment
    on_result: Callable[[InvoiceResult], None] | None = None


def discover(path: Path) -> tuple[list[Path], list[str]]:
    if path.is_file():
        if path.suffix.lower() not in SUPPORTED:
            raise ValueError("Unsupported invoice format.")
        return [path], []
    if not path.is_dir():
        raise ValueError("Invoice path does not exist or is not a file/directory.")
    entries = sorted(path.iterdir(), key=lambda p: (p.name, str(p)))
    paths = [p for p in entries if p.is_file() and p.suffix.lower() in SUPPORTED]
    if not paths:
        raise ValueError("No supported invoices found.")
    return paths, [str(p) for p in entries if p not in paths]


def run(
    paths: list[Path], dependencies: RunDependencies, skipped: list[str] | None = None
) -> RunResult:
    d = dependencies
    prepared: list[tuple[Path, IngestionOutcome]] = []
    results: list[InvoiceResult] = []
    fatal: ErrorInfo | None = None
    hashes: dict[str, str] = {}

    def record_result(result: InvoiceResult) -> None:
        result = result.model_copy(
            update={
                "source_sha256": hashes.get(result.source_id),
                "trace": [e for e in d.events.events if e.source_id == result.source_id],
            }
        )
        results.append(result)
        if d.on_result is not None:
            d.on_result(result)

    for position, path in enumerate(paths):
        source_id = f"source-{position + 1:04d}"
        d.events.source_id = source_id
        d.events.processing_index = None
        if fatal:
            outcome = IngestionOutcome(source_id=source_id, error=fatal)
        else:
            try:
                d.events.record(
                    "ingestion",
                    "ingest_started",
                    filename=path.name,
                    position=position + 1,
                    total=len(paths),
                )
                source = read_source(path, source_id)
                hashes[source_id] = source.content_sha256
                outcome = ingest(source, d.llm, d.policy, d.events)
            except AgentError as exc:
                if exc.info.code == ErrorCode.SOURCE_PARSE_FAILED:
                    finding = ValidationFinding(
                        code=exc.info.code,
                        severity=Severity.BLOCKER,
                        message=exc.info.message,
                        origin=FindingOrigin.SOURCE,
                    )
                    outcome = IngestionOutcome(
                        source_id=source_id, rejected=True, findings=[finding]
                    )
                else:
                    outcome = IngestionOutcome(source_id=source_id, error=exc.info)
            except Exception as exc:
                d.events.record("ingestion", "internal_error", exception_type=type(exc).__name__)
                outcome = IngestionOutcome(
                    source_id=source_id,
                    error=ErrorInfo(
                        code=ErrorCode.INTERNAL_ERROR,
                        message="Unexpected ingestion failure; invoice was not processed.",
                    ),
                )
        if outcome.error and outcome.error.fatal:
            fatal = outcome.error
        d.events.record(
            "ingestion",
            "ingest_terminal",
            rejected=outcome.rejected,
            error=outcome.error.code if outcome.error else None,
            attempts=outcome.attempts,
        )
        candidate = outcome.candidate
        if (
            not outcome.error
            and not outcome.rejected
            and candidate is not None
            and candidate.invoice_date is not None
        ):
            prepared.append((path, outcome))
        else:
            fields: dict[str, Any] = dict(
                run_id=d.events.run_id,
                source_id=source_id,
                source_path=str(path),
                findings=outcome.findings,
                candidate=candidate,
                attempts={"extraction": outcome.attempts},
            )
            if outcome.error:
                record_result(
                    InvoiceResult(
                        **fields,
                        status=ExecutionStatus.ERROR,
                        error=outcome.error,
                        reasons=[outcome.error.message],
                    )
                )
            else:
                record_result(
                    InvoiceResult(
                        **fields,
                        decision=Decision.REJECTED,
                        reasons=[f.message for f in outcome.findings]
                        or ["Invoice date could not be resolved before ordering."],
                    )
                )
    d.events.source_id = None
    d.events.record("batch", "ingestion_barrier", count=len(paths))
    prepared.sort(key=lambda pair: (pair[1].candidate.invoice_date, pair[0].name, str(pair[0])))  # type: ignore[union-attr]
    for index, (path, outcome) in enumerate(prepared):
        candidate = outcome.candidate
        assert candidate is not None
        d.events.source_id = candidate.source_id
        d.events.processing_index = index
        d.events.record(
            "processing", "processing_started", invoice_date=str(candidate.invoice_date)
        )
        if fatal:
            result = InvoiceResult(
                run_id=d.events.run_id,
                source_id=candidate.source_id,
                source_path=str(path),
                processing_index=index,
                status=ExecutionStatus.ERROR,
                error=fatal,
                reasons=["Not processed because shared infrastructure failed."],
            )
        else:
            try:
                result = process_invoice(
                    candidate, str(path), index, d.store, d.llm, d.policy, d.events, d.mock
                )
            except Exception as exc:
                d.events.record("processing", "internal_error", exception_type=type(exc).__name__)
                result = InvoiceResult(
                    run_id=d.events.run_id,
                    source_id=candidate.source_id,
                    source_path=str(path),
                    processing_index=index,
                    status=ExecutionStatus.ERROR,
                    error=ErrorInfo(
                        code=ErrorCode.INTERNAL_ERROR,
                        message="Unexpected processing failure; no success claimed.",
                    ),
                    reasons=["Unexpected processing failure."],
                )
            if result.error and result.error.fatal:
                fatal = result.error
        result = result.model_copy(
            update={
                "attempts": {**result.attempts, "extraction": outcome.attempts},
                "candidate": candidate,
            }
        )
        d.events.record(
            "processing",
            "processing_terminal",
            decision=result.decision,
            error=result.error.code if result.error else None,
        )
        record_result(result)
    d.events.source_id = None
    d.events.processing_index = None
    summary_error = None
    try:
        final_stock = d.store.snapshot().stock
    except AgentError as exc:
        final_stock = {}
        summary_error = exc.info
        d.events.record("batch", "final_inventory_unavailable", code=exc.info.code)
    results = [
        r.model_copy(update={"trace": [e for e in d.events.events if e.source_id == r.source_id]})
        for r in results
    ]
    paid = [r for r in results if r.payment.status == PaymentStatus.PAID]
    summary = RunSummary(
        run_id=d.events.run_id,
        error=summary_error,
        discovered=len(paths),
        completed=sum(r.status == ExecutionStatus.COMPLETED for r in results),
        approved=sum(r.decision == Decision.APPROVED for r in results),
        rejected=sum(r.decision == Decision.REJECTED for r in results),
        operational_errors=sum(r.status == ExecutionStatus.ERROR for r in results),
        duplicate_skips=sum(r.payment.status == PaymentStatus.ALREADY_PAID for r in results),
        new_payments=len(paid),
        total_paid_usd=sum((r.payment.amount_usd or Decimal(0) for r in paid), Decimal("0.00")),
        final_inventory=final_stock,
        skipped=skipped or [],
    )
    return RunResult(results=results, summary=summary, trace=d.events.events)
