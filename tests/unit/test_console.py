from decimal import Decimal
from io import StringIO
from pathlib import Path

from invoice_agent.console import ConsoleReporter, clean_terminal
from invoice_agent.models import (
    Decision,
    ErrorCode,
    ErrorInfo,
    ExecutionStatus,
    InvoiceResult,
    PaymentOutcome,
    PaymentStatus,
    RunResult,
    RunSummary,
    Severity,
    TraceEvent,
    ValidationFinding,
)


class FlushStream(StringIO):
    flushes = 0

    def flush(self) -> None:
        self.flushes += 1


def test_progress_flushes_immediately_without_repeating_extraction() -> None:
    output = FlushStream()
    reporter = ConsoleReporter([Path("invoice_1001.txt")], output)
    for name in ("ingest_started", "model_request", "ingest_terminal"):
        reporter.event(
            TraceEvent(run_id="r", source_id="source-0001", stage="ingestion", event=name)
        )
    assert output.getvalue().splitlines() == [
        "invoice_1001.txt: Extracting",
        "invoice_1001.txt: Extracted; waiting for date-ordered processing",
    ]
    assert output.flushes == 2


def test_trace_shows_timing_without_raw_model_payload() -> None:
    output = StringIO()
    reporter = ConsoleReporter([Path("invoice.txt")], output, trace=True)
    reporter.event(
        TraceEvent(
            run_id="r",
            source_id="source-0001",
            stage="vp_critique",
            event="model_response",
            payload={"elapsed_ms": 1500, "prompt": "must not appear"},
        )
    )
    assert output.getvalue() == "invoice.txt: Trace: vp_critique response received in 1.5s\n"


def test_rejection_deduplicates_reasons_and_includes_warnings_safely() -> None:
    output = StringIO()
    reporter = ConsoleReporter([], output, secrets=["private-value"])
    reporter.invoice(
        InvoiceResult(
            run_id="r",
            source_id="s",
            source_path="bad\nname.txt",
            decision=Decision.REJECTED,
            reasons=["Over stock", "Over stock"],
            findings=[
                ValidationFinding(code="STOCK", severity=Severity.BLOCKER, message="Over stock"),
                ValidationFinding(
                    code="TERMS",
                    severity=Severity.WARNING,
                    message="Due date\rconflict private-value\x1b[31m",
                ),
            ],
        ),
        output,
    )
    text = output.getvalue()
    assert len(text.splitlines()) == 3
    assert text.count("Over stock") == 1
    assert "Warning [TERMS]" in text
    assert "private-value" not in text
    assert "\r" not in text and "\x1b" not in text
    assert "bad name.txt: REJECTED; payment blocked" in text


def test_paid_duplicate_and_operational_failure_are_distinct() -> None:
    output = StringIO()
    reporter = ConsoleReporter([], output)
    for status in (PaymentStatus.PAID, PaymentStatus.ALREADY_PAID):
        reporter.invoice(
            InvoiceResult(
                run_id="r",
                source_id="s",
                source_path="invoice.txt",
                decision=Decision.APPROVED,
                payment=PaymentOutcome(status=status, payment_id="p", amount_usd=Decimal("5000")),
            ),
            output,
        )
    reporter.invoice(
        InvoiceResult(
            run_id="r",
            source_id="s",
            source_path="broken.txt",
            status=ExecutionStatus.ERROR,
            error=ErrorInfo(code=ErrorCode.PROVIDER_TRANSIENT, message="Provider unavailable"),
        ),
        output,
    )
    text = output.getvalue()
    assert "PAID (mock) — $5,000.00 USD" in text
    assert "ALREADY PAID in this run; duplicate skipped" in text
    assert "ERROR [PROVIDER_TRANSIENT]: Provider unavailable" in text


def test_summary_reports_summary_only_error_stock_and_skips() -> None:
    output = StringIO()
    reporter = ConsoleReporter([], output)
    reporter.summary(
        RunResult(
            results=[],
            summary=RunSummary(
                run_id="r",
                operational_errors=1,
                error=ErrorInfo(code=ErrorCode.STORAGE_ERROR, message="Snapshot failed"),
                final_inventory={"WidgetA": 2},
                skipped=["notes.md"],
            ),
        ),
        output,
    )
    text = output.getvalue()
    assert "1 invoice errors" in text
    assert "Run error [STORAGE_ERROR]: Snapshot failed" in text
    assert "Final inventory: WidgetA=2" in text
    assert "Skipped unsupported entry: notes.md" in text


def test_summary_only_failure_does_not_claim_success_or_zero_run_errors() -> None:
    output = StringIO()
    ConsoleReporter([], output).summary(
        RunResult(
            results=[],
            summary=RunSummary(
                run_id="r",
                error=ErrorInfo(code=ErrorCode.STORAGE_ERROR, message="Snapshot failed"),
            ),
        ),
        output,
    )
    text = output.getvalue()
    assert text.startswith("Run finished with errors:")
    assert "0 invoice errors" in text
    assert "0 operational errors" not in text
    assert "Final inventory unavailable" in text
    assert "Run error [STORAGE_ERROR]" in text


def test_retry_reports_failed_and_next_attempt() -> None:
    output = StringIO()
    ConsoleReporter([Path("invoice.txt")], output).event(
        TraceEvent(
            run_id="r",
            source_id="source-0001",
            stage="ingestion",
            event="transport_retry",
            payload={"attempt": 1},
        )
    )
    assert output.getvalue() == ("invoice.txt: Provider attempt 1 failed; retrying attempt 2\n")


def test_public_terminal_cleaner_handles_controls_and_secrets() -> None:
    assert clean_terminal("hello\r\nworld\x1b secret\u202e", ["secret", ""]) == (
        "hello world [REDACTED]"
    )
