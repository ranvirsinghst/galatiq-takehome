"""Small injectable interfaces; no provider SDK classes cross these boundaries."""

from decimal import Decimal
from typing import Protocol

from invoice_agent.models import (
    InventorySnapshot,
    InvoiceIdentity,
    LLMRequest,
    LLMResponse,
    MockPaymentResult,
    PaidRecord,
    PaymentOutcome,
    PaymentRequest,
    TraceEvent,
)


class LLMClient(Protocol):
    def complete(self, request: LLMRequest) -> LLMResponse: ...


class InventoryReader(Protocol):
    def lookup(self, items: list[str]) -> InventorySnapshot: ...


class MockPayment(Protocol):
    def __call__(self, vendor: str, amount_usd: Decimal) -> MockPaymentResult: ...


class PaymentStore(InventoryReader, Protocol):
    def find_paid(self, identity: InvoiceIdentity) -> PaidRecord | None: ...
    def pay(self, request: PaymentRequest, mock: MockPayment) -> PaymentOutcome: ...
    def snapshot(self) -> InventorySnapshot: ...
    def close(self) -> None: ...


class EventSink(Protocol):
    def emit(self, event: TraceEvent) -> None: ...


class NullEventSink:
    def emit(self, event: TraceEvent) -> None:
        pass
