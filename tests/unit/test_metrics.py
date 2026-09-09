"""Accounting tests use independent expected dollar arithmetic and actual contracts."""

from decimal import Decimal

import pytest

from invoice_agent.metrics import compute_metrics
from invoice_agent.models import (
    InvoiceCandidate,
    InvoiceIdentity,
    InvoiceResult,
    PaymentOutcome,
    TraceEvent,
)


def event(name, payload=None, stage="ingestion"):
    # Accounting is independently tested even before the numeric redaction allowlist.
    return TraceEvent.model_construct(
        run_id="r", event=name, stage=stage, payload=payload or {}, elapsed_ms=99999
    )


def telemetry(usage, model="grok-4.3"):
    return [
        event("model_request"),
        event("model_usage", {"resolved_model": model, "usage": usage}),
        event("model_call_finished", {"elapsed_ms": 123, "status": "success"}),
    ]


def invoice(source="a", amount="100", number="1", decision="rejected", paid=False):
    return InvoiceResult(
        run_id="r",
        source_id=source,
        source_path=source,
        identity=InvoiceIdentity(vendor="acme", invoice_number=number),
        total_usd=Decimal(amount) if amount else None,
        decision=decision,
        payment=PaymentOutcome(status="paid", payment_id="p") if paid else PaymentOutcome(),
    )


def test_cached_tokens_and_reasoning_not_double_counted():
    events = telemetry(
        dict(
            prompt_tokens=1000,
            completion_tokens=200,
            total_tokens=1200,
            cached_prompt_tokens=400,
            reasoning_tokens=150,
        )
    )
    m = compute_metrics([], events, 0, 200)
    assert m.estimated_api_cost_usd == Decimal("0.00133")
    assert (m.prompt_tokens, m.completion_tokens, m.total_tokens) == (1000, 200, 1200)
    assert (m.cached_prompt_tokens, m.reasoning_tokens) == (400, 150)
    assert m.usage_complete and m.cost_complete
    assert m.model_latency_ms == {"ingestion": 123}
    assert m.agent_latency_ms == {}
    assert m.model_calls == m.transport_attempts == 1


@pytest.mark.parametrize("prompt,expected", [(199999, "0.25024875"), (200000, "0.5005")])
def test_long_context_boundary(prompt, expected):
    m = compute_metrics(
        [],
        telemetry(dict(prompt_tokens=prompt, completion_tokens=100, total_tokens=prompt + 100)),
        0,
        1,
    )
    assert m.estimated_api_cost_usd == Decimal(expected)


def test_provider_ticks_precede_pricing_even_unknown_model():
    m = compute_metrics([], telemetry(dict(cost_in_usd_ticks=123456789), "unknown"), 0, 1)
    assert m.estimated_api_cost_usd == Decimal("0.0123456789")
    assert m.cost_complete and not m.usage_complete
    assert m.cost_basis == "provider cost_in_usd_ticks"


@pytest.mark.parametrize(
    "usage,model",
    [
        ({}, "grok-4.3"),
        ({"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3}, "unknown"),
        ({"prompt_tokens": True, "completion_tokens": -1}, "grok-4.3"),
        ({"prompt_tokens": 2, "completion_tokens": 1, "cached_prompt_tokens": 3}, "grok-4.3"),
    ],
)
def test_unpriced_usage_is_not_zero(usage, model):
    m = compute_metrics([], telemetry(usage, model), 0, 1)
    assert m.estimated_api_cost_usd is None
    assert not m.cost_complete


def test_transport_failure_reports_known_lower_bound_and_schema_response_once():
    events = telemetry(dict(prompt_tokens=1000, completion_tokens=1000, total_tokens=2000))
    events += [
        event("model_response", {"usage": {"total_tokens": 999999}}),
        event("model_request"),
        event("model_call_finished", {"elapsed_ms": 10, "status": "error"}, "review"),
    ]
    m = compute_metrics([], events, 0, 300)
    assert m.estimated_api_cost_usd == Decimal("0.00375")
    assert not m.cost_complete and not m.usage_complete
    assert m.total_tokens == 2000
    assert m.model_calls == m.transport_attempts == 2
    assert m.model_latency_ms == {"ingestion": 123, "review": 10}


def test_empty_run_zero_but_uninstrumented_double_unknown():
    assert compute_metrics([], [], 0, 0).estimated_api_cost_usd == 0
    m = compute_metrics([invoice()], [], 1, 0)
    assert m.estimated_api_cost_usd is None
    assert not m.usage_complete and not m.cost_complete


def test_exposure_deduplicates_and_excludes_paid_identity_regardless_of_order():
    results = [
        invoice("a", "100"),
        invoice("b", "200"),
        invoice("c", "300", "2"),
        invoice("d", "20", "2", "approved", True),
    ]
    m = compute_metrics(results, [], 4, 0)
    assert m.blocked_payment_exposure_usd == 200
    assert m.exposure_invoice_count == 1
    assert m.rejection_rate == 0.75


def test_candidate_identity_fallback_and_unvalued_exposure():
    base = invoice().model_copy(
        update={
            "identity": None,
            "total_usd": None,
            "candidate": InvoiceCandidate(
                source_id="a",
                vendor_normalized="acme",
                invoice_number_normalized="1",
                total_usd=Decimal("350"),
            ),
        }
    )
    bad = invoice("bad", None, "2")
    negative = invoice("negative", "-1", "3")
    m = compute_metrics([base, invoice(), bad, negative], [], 4, 0)
    assert m.blocked_payment_exposure_usd == 350
    assert m.exposure_invoice_count == 1
    assert m.exposure_unvalued_count == 2


def test_source_hash_deduplication_and_already_paid_exclusion():
    a = invoice().model_copy(update={"identity": None, "source_sha256": "same"})
    b = a.model_copy(update={"source_id": "b"})
    paid = invoice("p", "5", "2", "approved", True).model_copy(
        update={"payment": PaymentOutcome(status="already_paid", payment_id="p")}
    )
    m = compute_metrics([a, b, paid, invoice("other", "999", "2")], [], 4, 0)
    assert m.blocked_payment_exposure_usd == 100
    assert m.exposure_invoice_count == 1


def test_partial_run_rates_do_not_treat_rejections_as_errors():
    from invoice_agent.models import ErrorInfo

    failure = InvoiceResult(
        run_id="r",
        source_id="b",
        source_path="b",
        status="error",
        error=ErrorInfo(code="INTERNAL_ERROR", message="test"),
        total_usd=Decimal("900"),
    )
    m = compute_metrics([invoice(), failure], [], 10, 100, run_complete=False, run_error=True)
    assert not m.run_complete and m.run_error
    assert m.operational_error_rate == 0.1
    assert m.rejection_rate == 0.1
    assert m.blocked_payment_exposure_usd == 100


def test_failed_call_before_transport_is_incomplete():
    m = compute_metrics(
        [], [event("model_call_finished", {"elapsed_ms": 2, "status": "error"})], 0, 2
    )
    assert m.estimated_api_cost_usd is None
    assert not m.cost_complete and not m.usage_complete


@pytest.mark.parametrize(
    "changes", [{"total_tokens": 140}, {"cached_prompt_tokens": 101}, {"reasoning_tokens": 31}]
)
def test_incoherent_usage_not_priced_but_provider_ticks_still_authoritative(changes):
    usage = dict(prompt_tokens=100, completion_tokens=30, total_tokens=130)
    usage.update(changes)
    m = compute_metrics([], telemetry(usage), 0, 1)
    assert not m.usage_complete and not m.cost_complete
    assert m.estimated_api_cost_usd is None
    usage["cost_in_usd_ticks"] = 100000000
    m = compute_metrics([], telemetry(usage), 0, 1)
    assert not m.usage_complete and m.cost_complete
    assert m.estimated_api_cost_usd == Decimal("0.01")


def test_missing_total_still_prices_known_input_output_and_uses_configured_model():
    events = telemetry(dict(prompt_tokens=100, completion_tokens=30), model=None)
    events[1].payload["model"] = "grok-4.3"
    m = compute_metrics([], events, 0, 1)
    assert not m.usage_complete and m.cost_complete
    assert m.estimated_api_cost_usd == Decimal("0.0002")


def test_initialized_real_client_with_deterministic_rejection_is_known_zero():
    m = compute_metrics([invoice()], [event("model_client_initialized")], 1, 5)
    assert m.estimated_api_cost_usd == 0
    assert m.cost_complete and m.usage_complete
    assert m.cost_basis == "no model calls"
    assert m.model_calls == 0


def test_full_agent_latency_separate_from_model_calls_and_sums_retries():
    events = telemetry(dict(prompt_tokens=1, completion_tokens=1, total_tokens=2))
    events += [
        event("agent_stage_finished", {"elapsed_ms": 200}),
        event("agent_stage_finished", {"elapsed_ms": 50}),
        event("agent_stage_finished", {"elapsed_ms": 20}, "payment"),
    ]
    m = compute_metrics([], events, 0, 280)
    assert m.agent_latency_ms == {"ingestion": 250, "payment": 20}
    assert m.model_latency_ms == {"ingestion": 123}


def test_candidate_identity_uses_payment_normalization_for_paid_exclusion():
    rejected = InvoiceResult(
        run_id="r",
        source_id="rejected",
        source_path="rejected.json",
        decision="rejected",
        candidate=InvoiceCandidate(
            source_id="rejected",
            vendor_normalized="  Acme   Corp  ",
            invoice_number_normalized="inv  7",
            total_usd=Decimal("100"),
        ),
    )
    paid = invoice(decision="approved", paid=True).model_copy(
        update={
            "identity": InvoiceIdentity(vendor="acme corp", invoice_number="INV-7"),
        }
    )
    assert compute_metrics([rejected, paid], [], 2, 1).blocked_payment_exposure_usd == 0
