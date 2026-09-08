"""Readable, flushed terminal progress alongside durable structured artifacts."""

from __future__ import annotations

import unicodedata
from pathlib import Path
from typing import TextIO

from .models import InvoiceResult, PaymentStatus, RunResult, Severity, TraceEvent, redact


def clean_terminal(value: object, secrets: list[str] | None = None) -> str:
    """Render untrusted text on one terminal line without exposing configured secrets."""
    text = str(value)
    for secret in secrets or []:
        if secret:
            text = text.replace(secret, "[REDACTED]")
    text = str(redact(text))
    return " ".join(
        "".join(" " if unicodedata.category(c).startswith("C") else c for c in text).split()
    )


class ConsoleReporter:
    def __init__(
        self,
        paths: list[Path],
        stream: TextIO,
        trace: bool = False,
        secrets: list[str] | None = None,
    ) -> None:
        self.stream = stream
        self.trace = trace
        self.secrets = [secret for secret in secrets or [] if secret]
        self.names = {f"source-{i:04d}": path.name for i, path in enumerate(paths, 1)}

    def _clean(self, value: object) -> str:
        return clean_terminal(value, self.secrets)

    def _line(self, message: str, stream: TextIO | None = None) -> None:
        destination = self.stream if stream is None else stream
        destination.write(self._clean(message) + "\n")
        destination.flush()

    def event(self, event: TraceEvent) -> None:
        payload = event.payload
        name = self.names.get(event.source_id or "", event.source_id or "Run")
        message: str | None = None
        if event.event == "ingest_started":
            message = "Extracting"
            if payload.get("position") is not None and payload.get("total") is not None:
                message += f" ({payload['position']}/{payload['total']})"
        elif event.event == "ingest_terminal":
            if payload.get("error"):
                message = f"Extraction failed ({payload['error']})"
            elif payload.get("rejected"):
                message = "Extraction rejected"
            else:
                message = "Extracted; waiting for date-ordered processing"
        elif event.event == "ingestion_barrier":
            self._line("Run: Ingestion complete; processing invoices oldest first")
            return
        elif event.event == "processing_started":
            date = payload.get("invoice_date", payload.get("date"))
            message = f"Processing (invoice date {date})" if date else "Processing"
        elif event.event == "model_request":
            message = {
                "inventory": "Checking inventory",
                "vp_propose": "VP reviewing invoice",
                "vp_critique": "Checking VP decision",
                "vp_revise": "VP revising decision",
            }.get(event.stage)
        elif event.event == "extraction_repair_required":
            message = "Extraction needs correction; checking source evidence"
        elif event.event == "transport_retry":
            attempt = payload.get("attempt")
            message = (
                f"Provider attempt {attempt} failed; retrying attempt {attempt + 1}"
                if isinstance(attempt, int) and not isinstance(attempt, bool)
                else "Provider request failed; retrying"
            )
        elif event.event == "transport_exhausted":
            message = "Provider retry limit reached"
        elif self.trace and event.event == "tool_result":
            stock = payload.get("stock")
            if isinstance(stock, dict):
                message = "Trace: Inventory lookup: " + ", ".join(
                    f"{item}={quantity if quantity is not None else 'unknown'}"
                    for item, quantity in stock.items()
                )
        elif self.trace and event.event == "vp_critique":
            issues = payload.get("issues", [])
            count = len(issues) if isinstance(issues, list) else 0
            message = f"Trace: VP critique {payload.get('verdict', 'complete')}; {count} issues"
        elif self.trace and event.event == "model_response":
            elapsed = payload.get("elapsed_ms")
            duration = f" in {elapsed / 1000:.1f}s" if isinstance(elapsed, (int, float)) else ""
            message = f"Trace: {event.stage} response received{duration}"
        if message is not None:
            self._line(f"{name}: {message}")

    def invoice(self, item: InvoiceResult, stream: TextIO) -> None:
        name = Path(item.source_path).name
        if item.error:
            status = f"ERROR [{item.error.code}]: {item.error.message}"
        elif item.payment.status == PaymentStatus.PAID:
            amount = item.payment.amount_usd
            status = "PAID (mock)" + (f" — ${amount:,.2f} USD" if amount is not None else "")
        elif item.payment.status == PaymentStatus.ALREADY_PAID:
            status = "ALREADY PAID in this run; duplicate skipped"
        elif item.decision == "rejected":
            status = "REJECTED; payment blocked"
        else:
            status = "APPROVED; no payment committed"
        self._line(f"{name}: {status}", stream)
        details = list(item.reasons)
        details.extend(f.message for f in item.findings if f.severity == Severity.BLOCKER)
        seen: set[str] = set()
        for reason in details:
            cleaned = self._clean(reason)
            if cleaned and cleaned not in seen:
                self._line(f"{name}: Reason: {cleaned}", stream)
                seen.add(cleaned)
        for finding in item.findings:
            if finding.severity == Severity.WARNING:
                cleaned = self._clean(finding.message)
                if cleaned not in seen:
                    self._line(f"{name}: Warning [{finding.code}]: {cleaned}", stream)
                    seen.add(cleaned)

    def summary(self, result: RunResult, stream: TextIO) -> None:
        s = result.summary
        heading = "Run finished with errors" if s.error or s.operational_errors else "Run complete"
        self._line(
            f"{heading}: {s.discovered} invoices; {s.approved} approved; "
            f"{s.rejected} rejected; {s.duplicate_skips} duplicates skipped; "
            f"{s.operational_errors} invoice errors",
            stream,
        )
        self._line(f"Mock payments: {s.new_payments}; total ${s.total_paid_usd:,.2f} USD", stream)
        if s.final_inventory:
            stock = ", ".join(
                f"{name}={quantity if quantity is not None else 'unknown'}"
                for name, quantity in sorted(s.final_inventory.items())
            )
            self._line(f"Final inventory: {stock}", stream)
        elif s.error:
            self._line("Final inventory unavailable", stream)
        if s.error:
            self._line(f"Run error [{s.error.code}]: {s.error.message}", stream)
        for path in s.skipped:
            self._line(f"Skipped unsupported entry: {path}", stream)
