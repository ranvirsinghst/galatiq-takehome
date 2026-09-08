"""One event sink and JSONL writer keep diagnostics separate from results."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any, TextIO

from .models import InvoiceResult, RunResult, TraceEvent


class EventCollector:
    def __init__(
        self,
        run_id: str,
        secrets: list[str] | None = None,
        on_event: Callable[[TraceEvent], None] | None = None,
    ):
        self.on_event = on_event
        self.run_id = run_id
        self.events: list[TraceEvent] = []
        self.source_id: str | None = None
        self.processing_index: int | None = None
        self.start = time.monotonic()
        self.secrets = [value for value in secrets or [] if value]

    def emit(self, event: TraceEvent) -> None:
        payload = event.model_dump(mode="json")
        encoded = json.dumps(payload)
        for secret in self.secrets:
            encoded = encoded.replace(secret, "[REDACTED]")
        payload = json.loads(encoded)
        payload.update(
            run_id=self.run_id,
            source_id=event.source_id or self.source_id,
            sequence=len(self.events) + 1,
        )
        if event.elapsed_ms is None:
            payload["elapsed_ms"] = round((time.monotonic() - self.start) * 1000, 3)
        if self.processing_index is not None:
            payload["payload"]["processing_index"] = self.processing_index
        normalized = TraceEvent.model_validate(payload)
        self.events.append(normalized)
        if self.on_event is not None:
            try:
                self.on_event(normalized)
            except OSError:
                # Progress is best-effort; durable results/payment state remain authoritative.
                self.on_event = None
                self.record("observability", "progress_output_unavailable")

    def record(self, stage: str, event: str, **payload: Any) -> None:
        self.emit(
            TraceEvent(
                run_id=self.run_id,
                source_id=self.source_id,
                stage=stage,
                event=event,
                payload=payload,
            )
        )


def write_invoice(item: InvoiceResult, stream: TextIO, trace: bool = False) -> None:
    payload = item.model_dump(
        mode="json", exclude_none=True, exclude={"candidate", "validation", "review"}
    )
    if item.candidate is not None:
        c = item.candidate
        payload["extracted"] = {
            "vendor": c.vendor_normalized,
            "invoice_number": c.invoice_number_normalized,
            "invoice_date": str(c.invoice_date) if c.invoice_date else None,
            "due_date": str(c.due_date) if c.due_date else None,
            "currency": "USD",
            "total": str(c.total_usd) if c.total_usd is not None else None,
            "items": [
                {
                    "item": line.item_name_normalized,
                    "quantity": str(line.quantity) if line.quantity is not None else None,
                    "unit_price_usd": str(line.unit_price_usd)
                    if line.unit_price_usd is not None
                    else None,
                }
                for line in c.items
            ],
        }
    if not trace:
        payload.pop("trace", None)
    stream.write(json.dumps({"type": "invoice", **payload}, ensure_ascii=False) + "\n")
    stream.flush()


def write_summary(result: RunResult, stream: TextIO, trace: bool = False) -> None:
    summary = {"type": "summary", **result.summary.model_dump(mode="json")}
    if trace:
        summary["trace"] = [
            e.model_dump(mode="json", exclude_none=True)
            for e in result.trace
            if e.source_id is None
        ]
    stream.write(json.dumps(summary, ensure_ascii=False) + "\n")
    stream.flush()


def write_jsonl(result: RunResult, stream: TextIO, trace: bool = False) -> None:
    for item in result.results:
        write_invoice(item, stream, trace)
    write_summary(result, stream, trace)
