"""Conservative run accounting; blocked exposure is not realized loss savings."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from invoice_agent.models import InvoiceResult, TraceEvent


class RunMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    run_complete: bool = True
    wall_ms: float = 0
    agent_latency_ms: dict[str, float] = Field(default_factory=dict)
    model_latency_ms: dict[str, float] = Field(default_factory=dict)
    model_calls: int = 0
    transport_attempts: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cached_prompt_tokens: int = 0
    reasoning_tokens: int = 0
    estimated_api_cost_usd: Decimal | None = None
    cost_complete: bool = False
    usage_complete: bool = False
    cost_basis: str = "unavailable"
    operational_error_rate: float = 0
    rejection_rate: float = 0
    run_error: bool = False
    blocked_payment_exposure_usd: Decimal = Decimal("0")
    exposure_invoice_count: int = 0
    exposure_unvalued_count: int = 0


def _integer(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _identity(result: InvoiceResult) -> tuple[str, ...]:
    from .identity import payment_identity

    identity = result.identity or (payment_identity(result.candidate) if result.candidate else None)
    if identity:
        return ("invoice", identity.vendor.casefold(), identity.invoice_number.casefold())
    return ("source", result.source_sha256 or result.source_id)


def compute_metrics(
    results: list[InvoiceResult],
    events: list[TraceEvent],
    discovered: int,
    wall_ms: float,
    *,
    run_complete: bool = True,
    run_error: bool = False,
) -> RunMetrics:
    """Aggregate actual usage once per response; incomplete costs are known lower bounds.

    Pricing snapshot: xAI grok-4.3, 2026-09-08, USD per million tokens:
    input 1.25, cached input .20, output 2.50; double at >=200k prompt.
    Provider cost_in_usd_ticks takes precedence (one USD = 10 billion ticks).
    """
    names = (
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "cached_prompt_tokens",
        "reasoning_tokens",
    )
    totals = dict.fromkeys(names, 0)
    usage_events = [e for e in events if e.event == "model_usage"]
    calls = [e for e in events if e.event == "model_call_finished"]
    attempts = sum(e.event == "model_request" for e in events)
    latency: dict[str, float] = {}
    model_latency: dict[str, float] = {}
    for event in events:
        if event.event not in ("model_call_finished", "agent_stage_finished"):
            continue
        target = model_latency if event.event == "model_call_finished" else latency
        elapsed = event.payload.get("elapsed_ms")
        if isinstance(elapsed, (float, int)) and not isinstance(elapsed, bool) and elapsed >= 0:
            target[event.stage] = target.get(event.stage, 0.0) + elapsed
    # A provider double with decisions but no telemetry must not imply free operation.
    observed = bool(usage_events or calls or attempts)
    client_initialized = any(e.event == "model_client_initialized" for e in events)
    genuinely_empty = not observed and (not results or client_initialized)
    complete = (
        len(usage_events) == attempts and attempts >= len(calls) and (observed or genuinely_empty)
    )
    usage_complete = complete
    cost_complete = complete
    cost = Decimal("0")
    priced = 0
    bases: set[str] = set()
    for event in usage_events:
        usage = event.payload.get("usage", {})
        if not isinstance(usage, dict):
            usage = {}
        counts = {name: _integer(usage.get(name)) for name in names}
        for name, count in counts.items():
            totals[name] += count or 0
        prompt, output = counts["prompt_tokens"], counts["completion_tokens"]
        if any(counts[name] is None for name in names[:3]):
            usage_complete = False
        cached = counts["cached_prompt_tokens"] or 0
        total = counts["total_tokens"]
        reasoning = counts["reasoning_tokens"] or 0
        coherent = (
            prompt is not None
            and output is not None
            and cached <= prompt
            and reasoning <= output
            and (total is None or total == prompt + output)
        )
        if not coherent:
            usage_complete = False
        ticks = _integer(usage.get("cost_in_usd_ticks"))
        if ticks is not None:
            cost += Decimal(ticks) / Decimal("10000000000")
            priced += 1
            bases.add("provider cost_in_usd_ticks")
        elif (
            (event.payload.get("resolved_model") or event.payload.get("model")) == "grok-4.3"
            and prompt is not None
            and output is not None
            and coherent
        ):
            multiplier = 2 if prompt >= 200000 else 1
            cost += (
                (
                    Decimal(prompt - cached) * Decimal("1.25")
                    + Decimal(cached) * Decimal("0.20")
                    + Decimal(output) * Decimal("2.50")
                )
                * multiplier
                / Decimal("1000000")
            )
            priced += 1
            bases.add("grok-4.3 pricing snapshot 2026-09-08")
        else:
            cost_complete = False
    paid = {_identity(r) for r in results if r.payment.status in ("paid", "already_paid")}
    amounts: dict[tuple[str, ...], Decimal | None] = {}
    for result in results:
        if result.status != "completed" or result.decision != "rejected":
            continue
        key = _identity(result)
        if key in paid:
            continue
        amount = result.total_usd
        if amount is None and result.candidate:
            amount = result.candidate.total_usd
        valid = amount is not None and amount.is_finite() and amount > 0
        previous = amounts.get(key)
        if valid and amount is not None:
            amounts[key] = max(previous or Decimal("0"), amount)
        elif key not in amounts:
            amounts[key] = None
    errors = sum(r.status == "error" for r in results)
    rejected = sum(r.status == "completed" and r.decision == "rejected" for r in results)
    return RunMetrics(
        run_complete=run_complete,
        wall_ms=wall_ms,
        agent_latency_ms=latency,
        model_latency_ms=model_latency,
        model_calls=len(calls),
        transport_attempts=attempts,
        **totals,
        estimated_api_cost_usd=cost if priced or genuinely_empty else None,
        cost_complete=cost_complete,
        usage_complete=usage_complete,
        cost_basis=" + ".join(sorted(bases))
        if bases
        else ("no model calls" if genuinely_empty else "unavailable"),
        operational_error_rate=errors / discovered if discovered else 0,
        rejection_rate=rejected / discovered if discovered else 0,
        run_error=run_error,
        blocked_payment_exposure_usd=sum(
            (v for v in amounts.values() if v is not None), Decimal("0")
        ),
        exposure_invoice_count=sum(v is not None for v in amounts.values()),
        exposure_unvalued_count=sum(v is None for v in amounts.values()),
    )
