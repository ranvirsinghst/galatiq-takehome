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
    for name in ("ingest_started", "model_request", "ingest_terminal", "ingestion_barrier"):
        reporter.event(
            TraceEvent(run_id="r", source_id="source-0001", stage="ingestion", event=name)
        )
    assert output.getvalue().splitlines() == [
        "invoice_1001.txt: Reading invoice",
    ]
    assert output.flushes == 1


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
    assert "Note:" in text
    assert "private-value" not in text
    assert "\r" not in text and "\x1b" not in text
    assert "bad name.txt: Rejected; no payment made" in text


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
    assert "Paid (simulated) — $5,000.00 USD" in text
    assert "Duplicate skipped; already paid in this run" in text
    assert "Could not complete: The invoice review service is unavailable. Try again later." in text


def test_summary_reports_summary_only_error_stock_and_skips() -> None:
    output = StringIO()
    reporter = ConsoleReporter([], output, trace=True)
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
    ConsoleReporter([], output, trace=True).summary(
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
    assert output.getvalue() == ("invoice.txt: Service unavailable; trying again (attempt 2)\n")


def test_public_terminal_cleaner_handles_controls_and_secrets() -> None:
    assert clean_terminal("hello\r\nworld\x1b secret\u202e", ["secret", ""]) == (
        "hello world [REDACTED]"
    )


def test_final_table_preserves_order_alignment_and_safe_truncation() -> None:
    output = StringIO()
    items = [
        InvoiceResult(
            run_id="r",
            source_id="s1",
            source_path="/tmp/z.txt",
            decision=Decision.APPROVED,
            payment=PaymentOutcome(
                status=PaymentStatus.PAID, payment_id="p", amount_usd=Decimal("5000")
            ),
        ),
        InvoiceResult(
            run_id="r",
            source_id="s2",
            source_path="/tmp/a.txt",
            decision=Decision.APPROVED,
            payment=PaymentOutcome(status=PaymentStatus.ALREADY_PAID, payment_id="p"),
        ),
        InvoiceResult(
            run_id="r",
            source_id="s3",
            source_path="long\n" + "x" * 40 + ".txt",
            decision=Decision.REJECTED,
            total_usd=Decimal("12.34"),
            reasons=["private-value\r\n" + "too many | items " * 10],
        ),
        InvoiceResult(
            run_id="r",
            source_id="s4",
            source_path="broken.txt",
            status=ExecutionStatus.ERROR,
            error=ErrorInfo(code=ErrorCode.PROVIDER_TRANSIENT, message="Provider unavailable"),
        ),
    ]
    ConsoleReporter([], output, trace=True, secrets=["private-value"]).summary(
        RunResult(results=items, summary=RunSummary(run_id="r")), output
    )
    lines = output.getvalue().splitlines()
    assert "Filename" in lines[0] and "Primary reason" in lines[0]
    rows = lines[2:6]
    assert [line.split(" | ")[1].strip() for line in rows] == [
        "PAID",
        "DUPLICATE",
        "REJECTED",
        "ERROR",
    ]
    assert "$5,000.00" in rows[0] and "$12.34" in rows[2]
    assert "—" in rows[1] and "—" in rows[3]
    assert "…" in rows[2] and "private-value" not in output.getvalue()
    assert all(len(line) <= 108 for line in lines[:6])
    assert all([i for i, c in enumerate(line) if c == "|"] == [27, 39, 58] for line in rows)


def test_summary_metrics_distinguishes_estimates_exposure_and_errors() -> None:
    from invoice_agent.models import RunMetrics

    output = StringIO()
    metrics = RunMetrics(
        run_complete=True,
        wall_ms=5000,
        agent_latency_ms={"ingestion": 2500},
        model_latency_ms={"vp_propose": 1200},
        model_calls=2,
        transport_attempts=3,
        prompt_tokens=1000,
        completion_tokens=200,
        total_tokens=1200,
        cached_prompt_tokens=100,
        reasoning_tokens=50,
        estimated_api_cost_usd=Decimal("0.000321"),
        cost_complete=True,
        usage_complete=True,
        operational_error_rate=0.1,
        rejection_rate=0.4,
        run_error=False,
        blocked_payment_exposure_usd=Decimal("5000"),
        exposure_invoice_count=2,
        exposure_unvalued_count=1,
    )
    ConsoleReporter([], output, trace=True).summary(
        RunResult(results=[], summary=RunSummary(run_id="r", metrics=metrics)), output
    )
    text = output.getvalue()
    assert "Estimated token spend: $0.000321 USD (complete)" in text
    assert "$5,000.00 USD across 2 invoices; not realized savings" in text
    assert "excludes 1 rejected invoices" in text
    assert "1,000 input (100 cached); 200 output (50 reasoning); 1,200 total" in text
    assert "5.0s overall; 2 model calls; 3 transport attempts" in text
    assert "ingestion 2.5s" in text
    assert "Model-call time by phase: vp_propose 1.2s" in text
    assert "Processing error rate: 10.0%; business rejection rate: 40.0%; run error: no" in text


def test_partial_unavailable_metrics_do_not_claim_complete_cost() -> None:
    from invoice_agent.models import RunMetrics

    for cost, expected in ((None, "unavailable"), (Decimal("0.001"), "$0.001000 USD (partial")):
        output = StringIO()
        metrics = RunMetrics(
            run_complete=False,
            estimated_api_cost_usd=cost,
            cost_complete=False,
            usage_complete=False,
        )
        ConsoleReporter([], output, trace=True).summary(
            RunResult(results=[], summary=RunSummary(run_id="r", metrics=metrics)), output
        )
        assert f"Estimated token spend: {expected}" in output.getvalue()
        assert "Tokens (partial)" in output.getvalue()
        assert "Metrics cover a partial run" in output.getvalue()


def test_table_aligns_wide_and_combining_text_and_uses_candidate_amount() -> None:
    from invoice_agent.console import terminal_width
    from invoice_agent.models import InvoiceCandidate

    output = StringIO()
    item = InvoiceResult(
        run_id="r",
        source_id="s",
        source_path="票据" * 20 + ".txt",
        decision=Decision.REJECTED,
        reasons=["e\u0301" * 60],
        candidate=InvoiceCandidate(source_id="s", total_usd=Decimal("123.45")),
    )
    ConsoleReporter([], output, trace=True).summary(
        RunResult(results=[item], summary=RunSummary(run_id="r")), output
    )
    row = output.getvalue().splitlines()[2]
    assert [terminal_width(cell) for cell in row.split(" | ")] == [26, 9, 16, 48]
    assert "$123.45" in row and row.count("…") == 2
    assert terminal_width(row) == 108


def test_trace_write_failure_is_visible_without_trace_option() -> None:
    output = StringIO()
    ConsoleReporter([], output).event(
        TraceEvent(run_id="r", stage="output", event="trace_output_unavailable")
    )
    assert "Warning: Activity log could not be saved" in output.getvalue()


def test_default_output_bounds_details_and_omits_internal_steps() -> None:
    output = StringIO()
    reporter = ConsoleReporter([Path("invoice.txt")], output)
    for phase in ("inventory", "vp_propose", "vp_critique"):
        reporter.event(
            TraceEvent(run_id="r", source_id="source-0001", stage=phase, event="model_request")
        )
    assert output.getvalue() == ""
    item = InvoiceResult(
        run_id="r",
        source_id="source-0001",
        source_path="invoice.txt",
        decision=Decision.REJECTED,
        reasons=["Long explanation " * 100, "Another explanation", "More explanation"],
        findings=[
            ValidationFinding(
                code="STOCK", severity=Severity.BLOCKER, message="WidgetB requires 20, available 5"
            )
        ],
    )
    reporter.invoice(item, output)
    lines = output.getvalue().splitlines()
    assert len(lines) == 4
    assert "WidgetB requires 20, available 5" in lines[1]
    assert lines[2].endswith("…")
    assert lines[3] == "invoice.txt: More details in the saved report."
    assert len(lines[2]) < 210
    assert len(item.reasons) == 3  # Presentation does not discard saved evidence.


def test_default_summary_is_short_and_preserves_failure_and_skipped_notice() -> None:
    from invoice_agent.models import RunMetrics

    output = StringIO()
    ConsoleReporter([], output).summary(
        RunResult(
            results=[],
            summary=RunSummary(
                run_id="r",
                metrics=RunMetrics(wall_ms=1500, run_complete=False),
                error=ErrorInfo(
                    code=ErrorCode.STORAGE_ERROR, message="Unable to save activity log"
                ),
                skipped=["a.md", "b.md"],
            ),
        ),
        output,
    )
    text = output.getvalue()
    assert len(text.splitlines()) == 6
    assert text.startswith("Run finished with errors:")
    assert "Time taken: 1.5s" in text
    assert "results are incomplete" in text
    assert "Unable to save activity log" in text
    assert "Skipped 2 unsupported entries" in text
    assert "Tokens" not in text and "Filename" not in text


def test_paid_output_does_not_repeat_approval_rationale() -> None:
    output = StringIO()
    ConsoleReporter([], output).invoice(
        InvoiceResult(
            run_id="r",
            source_id="s",
            source_path="invoice.txt",
            decision=Decision.APPROVED,
            reasons=["All validation checks passed. " * 100],
            payment=PaymentOutcome(
                status=PaymentStatus.PAID, payment_id="p", amount_usd=Decimal("12.50")
            ),
        ),
        output,
    )
    assert output.getvalue().splitlines() == ["invoice.txt: Paid (simulated) — $12.50 USD"]


def test_error_keeps_distinct_skip_reason_and_points_to_full_details() -> None:
    output = StringIO()
    ConsoleReporter([], output).invoice(
        InvoiceResult(
            run_id="r",
            source_id="s",
            source_path="invoice.txt",
            status=ExecutionStatus.ERROR,
            error=ErrorInfo(code=ErrorCode.STORAGE_ERROR, message="Storage issue " * 100),
            reasons=["Not processed because shared infrastructure failed."],
        ),
        output,
    )
    text = output.getvalue()
    assert "Not processed because shared infrastructure failed." in text
    assert "More details in the saved report." in text


def test_reasons_with_same_shortened_text_are_not_repeated() -> None:
    output = StringIO()
    shared = "Unclear invoice details " * 20
    ConsoleReporter([], output).invoice(
        InvoiceResult(
            run_id="r",
            source_id="s",
            source_path="invoice.txt",
            decision=Decision.REJECTED,
            reasons=[shared + "one", shared + "two"],
            findings=[
                ValidationFinding(code=str(i), severity=Severity.WARNING, message=f"Warning {i}")
                for i in range(3)
            ],
        ),
        output,
    )
    text = output.getvalue()
    assert text.count("Reason:") == 1
    assert text.count("Note:") == 1
    assert "More details in the saved report." in text
