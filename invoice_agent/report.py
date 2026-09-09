"""Self-contained run reports, rendered from recorded outcomes and public audit events."""

from __future__ import annotations

import json
import os
from decimal import Decimal
from html import escape
from pathlib import Path
from typing import Any

from .models import InvoiceResult, RunResult, TraceEvent

_CSS = """
:root{color-scheme:light;--ink:#182e32;--muted:#52676b;--line:#cdd9d5;--paper:#f3f5ee;--accent:#176656}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.55 system-ui,sans-serif}
main{max-width:1180px;margin:auto;padding:48px 28px}header{border-top:6px solid var(--accent);padding-top:24px;margin-bottom:32px}
h1{font-size:clamp(28px,4vw,40px);line-height:1.2;font-weight:650;margin:12px 0}h2{font-size:22px;font-weight:650;margin:32px 0 16px}h3{margin:0;font-size:20px}h4{font-size:16px;margin:16px 0 6px}.vp-review{padding:0 16px 16px;background:#f6f8f6;border:1px solid var(--line);border-radius:6px}.review-step{border-top:1px solid var(--line);margin-top:16px;padding-top:4px}
p{margin:8px 0}.eyebrow{text-transform:uppercase;letter-spacing:.16em;font-size:12px;font-weight:750}.muted,small{color:var(--muted)}
.metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}.metric{padding:20px;background:white;border:1px solid var(--line);border-radius:8px}.metric strong{display:block;font-size:28px;line-height:1.3;margin:8px 0;font-variant-numeric:tabular-nums}.metric small{display:block}
.banner{padding:16px 20px;background:#fff1d6;border-left:4px solid #9b631a;margin:16px 0}.strip{display:flex;gap:12px 28px;flex-wrap:wrap;padding:16px 0;border-bottom:1px solid var(--line)}
.invoice{margin:16px 0;padding:24px;background:#fff;border:1px solid var(--line);border-radius:8px}.invoice-head{display:flex;justify-content:space-between;align-items:start;gap:20px}.amount{font-size:24px;font-weight:700;white-space:nowrap;font-variant-numeric:tabular-nums}.badge{display:inline-block;font-size:13px;font-weight:700;border:1px solid var(--line);padding:3px 10px;border-radius:20px;margin:8px 6px 8px 0}.paid{background:#e5f3eb}.rejected,.error,.failed{background:#ffebdf}.already_paid{background:#e8eefb}
summary{cursor:pointer;padding:12px 0;font-weight:650}summary:hover{color:var(--accent)}summary:focus-visible,a:focus-visible{outline:3px solid #bc721c;outline-offset:4px}details{border-top:1px solid var(--line);margin-top:12px}details details{margin:4px 0}.timeline{padding-left:28px}.timeline>li{padding:0 0 16px 12px;border-left:2px solid var(--line)}.timeline>li::marker{font-weight:700;color:var(--accent)}.event-head{display:flex;justify-content:space-between;gap:16px}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f3f6f5;padding:16px;font:13px/1.6 ui-monospace,monospace;border-radius:6px}li,p,h3,small,code{overflow-wrap:anywhere}dl{display:grid;grid-template-columns:minmax(140px,1fr) 2fr;gap:6px 20px}dt{color:var(--muted)}dd{margin:0}a{color:var(--accent)}footer{margin-top:40px;padding-top:16px;border-top:1px solid var(--line);font-size:13px;color:var(--muted)}
@media(max-width:720px){main{padding:24px 16px}.metrics{grid-template-columns:repeat(2,minmax(0,1fr))}.invoice-head{display:block}.invoice{padding:16px}.event-head{display:block}dl{grid-template-columns:1fr}dd{margin-bottom:8px}}
@media print{body{background:white}main{padding:0}.metrics{grid-template-columns:repeat(4,1fr)}.invoice{break-inside:avoid}summary{color:var(--ink)}}
"""


def _money(value: Decimal | None) -> str:
    return "Unavailable" if value is None else f"${value:,.2f} USD"


def render_report(result: RunResult, *, secrets: list[str] | None = None) -> str:
    """Escape all dynamic text and redact configured credentials before HTML encoding."""

    def redacted(value: Any) -> Any:
        if isinstance(value, dict):
            return {redacted(str(key)): redacted(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [redacted(item) for item in value]
        if not isinstance(value, str):
            return value
        rendered = value
        for secret in secrets or []:
            if secret:
                rendered = rendered.replace(secret, "[REDACTED]")
        return rendered

    def text(value: Any) -> str:
        return escape(str(redacted(value)), quote=True)

    def label(value: Any) -> str:
        return text(str(value).replace("_", " ").capitalize())

    def raw(value: Any) -> str:
        return (
            "<pre>"
            + text(json.dumps(redacted(value), ensure_ascii=False, indent=2, default=str))
            + "</pre>"
        )

    def metric(title: str, value: Any, note: str) -> str:
        return f'<div class="metric"><span>{text(title)}</span><strong>{text(value)}</strong><small>{text(note)}</small></div>'

    event_titles = {
        "vp_propose": "VP initial proposal",
        "vp_critique": "VP critique",
        "vp_revise": "VP revised proposal",
        "payment_committed": "Payment committed to ledger",
        "ingestion_barrier": "All invoice ingestion finished",
        "model_usage": "Model token usage and spend",
        "agent_stage_finished": "Agent stage finished",
    }

    def timeline(events: list[TraceEvent]) -> str:
        if not events:
            return '<p class="muted">No recorded process events available.</p>'
        steps = []
        for event in sorted(events, key=lambda e: e.sequence):
            elapsed = (
                f"{event.elapsed_ms / 1000:.2f}s from run start"
                if event.elapsed_ms is not None
                else "Time unavailable"
            )
            # Show the actual public decision and feedback before technical payloads.
            highlights = "".join(
                f"<b>{label(key)}:</b>{paragraphs(event.payload[key])}"
                for key in ("decision", "reason", "verdict", "issues", "required_changes")
                if event.payload.get(key) is not None
            )
            title = event_titles.get(event.event, event.event.replace("_", " ").capitalize())
            if (
                event.event in ("tool_requested", "tool_result")
                and event.payload.get("name") == "lookup_price"
            ):
                title = (
                    "Catalog price lookup requested"
                    if event.event == "tool_requested"
                    else "Catalog price lookup result"
                )
            steps.append(
                f'<li><div class="event-head"><b>{text(title)}</b><small>#{event.sequence} · {text(elapsed)}</small></div>'
                f"<small>{text(event.stage)} · <code>{text(event.event)}</code> · {text(event.severity)}"
                f"{' · ' + text(event.error_code) if event.error_code else ''}</small>{highlights}"
                f"<details><summary>Recorded evidence</summary>{raw(event.payload)}</details></li>"
            )
        return '<ol class="timeline">' + "".join(steps) + "</ol>"

    def paragraphs(value: Any) -> str:
        """Readable model text and feedback; never render untrusted strings as markup."""
        if isinstance(value, list):
            return "<ul>" + "".join(f"<li>{text(part)}</li>" for part in value) + "</ul>"
        return f"<p>{text(value)}</p>"

    def vp_review(item: InvoiceResult | None, events: list[TraceEvent]) -> str:
        review = item.review if item else None
        vp_events = sorted(
            [
                e
                for e in events
                if e.stage == "vp"
                and e.event in ("vp_propose", "vp_critique", "vp_revise", "response_error")
            ],
            key=lambda e: e.sequence,
        )
        heading = "VP review"
        if review is not None:
            if review.error:
                status = "Incomplete — VP review failed"
            elif review.accepted and review.proposal:
                status = f"Final VP decision: {review.proposal.decision}"
            else:
                status = "Unresolved — no accepted VP decision"
        elif any(e.event == "review_rejected_by_rules" for e in events):
            status = "Skipped — deterministic checks blocked payment"
        elif item and item.payment.status == "already_paid":
            status = "Skipped — equivalent invoice already paid"
        elif vp_events:
            status = "Incomplete — no final VP review outcome recorded"
        else:
            status = "No VP review recorded"
        parts = [
            f'<details class="vp-review" open><summary>{heading}</summary>',
            f"<p><b>{text(status)}</b></p>",
        ]
        if review:
            parts.append(
                f'<p class="muted">{review.semantic_calls} model calls · {review.revision_count} revisions. VP review acceptance requires both critique acceptance and deterministic safeguards.</p>'
            )
            if review.error:
                parts.append(paragraphs(f"{review.error.code}: {review.error.message}"))
            elif not review.accepted and review.rejection_reasons:
                parts.append(paragraphs(review.rejection_reasons))
        for event in vp_events:
            payload = event.payload
            title = event_titles.get(event.event, "VP response error")
            parts.append(
                f'<section class="review-step"><h4>{text(title)} · Round {text(payload.get("cycle", "unknown"))}</h4>'
            )
            actor = "Review system" if event.event == "response_error" else "VP agent"
            parts.append(f"<small>{actor} · event #{event.sequence}</small>")
            if event.event in ("vp_propose", "vp_revise"):
                parts.append(
                    f"<p><b>Proposed decision: {label(payload.get('decision', 'unavailable'))}</b></p>"
                )
            elif event.event == "vp_critique":
                parts.append(
                    f"<p><b>Agent verdict: {label(payload.get('verdict', 'unavailable'))}</b></p>"
                )
            rationale = payload.get("reason") or payload.get("reason_summary")
            parts.append(paragraphs(rationale or "Rationale was not recorded for this step."))
            parts.append(check_summaries(payload.get("checks")))
            for field, title in (
                ("agent_issues", "Agent concerns"),
                ("required_changes", "Requested changes"),
                ("deterministic_issues", "Deterministic safeguards"),
            ):
                if payload.get(field):
                    parts.append(f"<b>{title}</b>" + paragraphs(payload[field]))
            if "agent_issues" not in payload and payload.get("issues"):
                parts.append(
                    "<b>Recorded feedback (agent and rule checks combined)</b>"
                    + paragraphs(payload["issues"])
                )
            if event.event == "vp_critique":
                if payload.get("accepted") is True:
                    parts.append("<p><b>Round result: review accepted.</b></p>")
                elif payload.get("accepted") is False:
                    parts.append("<p><b>Round result: not accepted.</b></p>")
                else:
                    parts.append(
                        '<p class="muted">Effective round acceptance was not recorded; the agent verdict alone does not authorize payment.</p>'
                    )
            if payload.get("finding_codes"):
                parts.append(
                    "<p><b>Findings cited:</b> "
                    + text(", ".join(str(v) for v in payload["finding_codes"]))
                    + "</p>"
                )
            if payload.get("evidence_refs"):
                parts.append(
                    "<p><b>Evidence references:</b> "
                    + text(", ".join(str(v) for v in payload["evidence_refs"]))
                    + "</p>"
                )
            parts.append("</section>")
        if review:
            # Historical audits may retain the last review without its event history.
            parts.append(
                '<p class="muted">Final saved review record. These explanations are retained even when event history is incomplete.</p>'
            )
            if review.proposal:
                parts.append("<h4>Final recorded proposal · VP agent</h4>")
                parts.append(paragraphs(review.proposal.reason_summary))
                parts.append(check_summaries(review.proposal.checks))
            if review.critique:
                parts.append(f"<h4>Final recorded critique · {label(review.critique.verdict)}</h4>")
                parts.append(
                    paragraphs(
                        review.critique.reason_summary
                        or "Critique rationale was not recorded in this run."
                    )
                )
                if review.critique.issues:
                    parts.append("<b>Agent concerns</b>" + paragraphs(review.critique.issues))
                if review.critique.required_changes:
                    parts.append(
                        "<b>Requested changes</b>" + paragraphs(review.critique.required_changes)
                    )
        parts.append("</details>")
        return "".join(parts)

    def check_summaries(checks: Any) -> str:
        if not isinstance(checks, dict) or not checks:
            return ""
        return (
            "<dl>"
            + "".join(
                f"<dt>{label(name)}</dt><dd>{text(explanation)}</dd>"
                for name, explanation in checks.items()
            )
            + "</dl>"
        )

    def source_context(item: InvoiceResult, events: list[TraceEvent]) -> str:
        candidate = item.candidate
        repairs = [e for e in events if e.event == "extraction_repair_required"]
        normalizations = list(candidate.normalizations) if candidate else []
        if candidate:
            for line in candidate.items:
                normalizations.extend(line.normalizations)
        if not repairs and not normalizations and not (candidate and candidate.assumptions):
            return ""
        parts = ["<details><summary>Source processing · recorded facts</summary>"]
        for event in repairs:
            parts.append(
                f"<h4>Source checks requested corrections · attempt {text(event.payload.get('attempt', 'unknown'))}</h4>"
            )
            parts.append(paragraphs(event.payload.get("issues", [])))
        if normalizations:
            parts.append("<h4>Recorded normalization</h4><ul>")
            for entry in normalizations:
                parts.append(
                    f"<li><b>{label(entry.field)}:</b> {text(entry.original_value)} → {text(entry.normalized_value)}. Method: {text(entry.method)}.</li>"
                )
            parts.append("</ul>")
        if candidate and candidate.assumptions:
            parts.append("<h4>Recorded assumptions</h4>" + paragraphs(candidate.assumptions))
        parts.append("</details>")
        return "".join(parts)

    def validation_context(item: InvoiceResult) -> str:
        report = item.validation
        if report is None:
            return ""
        parts = ["<details><summary>Validation evidence · deterministic checks</summary>"]
        if report.findings:
            parts.append(
                "<ul>"
                + "".join(
                    f"<li><b>{label(f.severity)} · {label(f.origin)}:</b> {text(f.message)}</li>"
                    for f in report.findings
                )
                + "</ul>"
            )
        else:
            parts.append("<p>No validation findings were recorded.</p>")
        if report.aggregate_quantities:
            parts.append(
                "<h4>Inventory evidence used for review</h4><ul>"
                + "".join(
                    f"<li>{text(name)}: {quantity} requested; {text(report.stock_snapshot.stock.get(name) if report.stock_snapshot.stock.get(name) is not None else 'unavailable')} in stock.</li>"
                    for name, quantity in report.aggregate_quantities.items()
                )
                + "</ul>"
            )
        if report.catalog_evidence is not None:
            parts.append(
                "<h4>Catalog price evidence used for review (USD/unit)</h4><ul>"
                + "".join(
                    f"<li>{text(name)}: {text(price if price is not None else 'unavailable')}</li>"
                    for name, price in report.catalog_evidence.prices.items()
                )
                + "</ul>"
            )
            parts.append(
                "<b>Price checks performed</b>"
                + paragraphs([c for c in report.performed_checks if c.startswith("price:")])
            )
        if report.unavailable_checks:
            parts.append("<b>Checks unavailable</b>" + paragraphs(report.unavailable_checks))
        parts.append("</details>")
        return "".join(parts)

    def invoice(item: InvoiceResult, index: int) -> str:
        candidate = item.candidate
        identity = item.identity
        vendor = identity.vendor if identity else candidate.vendor_normalized if candidate else None
        number = (
            identity.invoice_number
            if identity
            else candidate.invoice_number_normalized
            if candidate
            else None
        )
        amount = (
            item.total_usd
            if item.total_usd is not None
            else candidate.total_usd
            if candidate
            else None
        )
        decision = item.decision or item.status
        events = [event for event in result.trace if event.source_id == item.source_id]
        if not events:
            events = item.trace or []
        reasons = "".join(f"<li>{text(reason)}</li>" for reason in item.reasons)
        findings = "".join(
            f"<li><b>{label(f.origin)} · {text(f.code)}</b>: {text(f.message)}"
            f"{raw({'observed': f.observed, 'expected': f.expected, 'evidence_refs': f.evidence_refs})}</li>"
            for f in item.findings
        )
        evidence = {
            "candidate": candidate.model_dump(mode="json") if candidate else None,
            "validation": item.validation.model_dump(mode="json") if item.validation else None,
            "review": item.review.model_dump(mode="json") if item.review else None,
        }
        return (
            f'<article class="invoice" id="invoice-{index}"><div class="invoice-head"><div>'
            f'<div class="eyebrow">Invoice {index}</div><h3>{text(vendor or "Vendor unavailable")} · {text(number or "Number unavailable")}</h3>'
            f"<small>{text(item.source_path)} · {text(item.source_id)}</small></div>"
            f'<div class="amount">{text(_money(amount))}<small style="display:block;font-size:12px;font-weight:400">Invoice amount · normalized USD</small></div></div>'
            f'<span class="badge {text(decision)}">{label(decision)}</span>'
            f'<span class="badge {text(item.payment.status)}">{label(item.payment.status)} (mock payment)</span>'
            f"<ul>{reasons}</ul>"
            f'<p class="muted">Recorded payment amount: {text(_money(item.payment.amount_usd))}'
            f" · Payment ID: {text(item.payment.payment_id or 'None')}</p>"
            + (
                f'<p class="banner">{text(item.error.code)}: {text(item.error.message)}</p>'
                if item.error
                else ""
            )
            + vp_review(item, events)
            + source_context(item, events)
            + validation_context(item)
            + f"<details><summary>Agent process · {len(events)} recorded steps</summary>{timeline(events)}</details>"
            + (
                f"<details><summary>Validation findings · {len(item.findings)}</summary><ul>{findings}</ul></details>"
                if findings
                else ""
            )
            + f"<details><summary>Source facts and final review</summary>{raw(evidence)}</details></article>"
        )

    summary = result.summary
    metrics = summary.metrics
    complete = metrics is not None and metrics.run_complete
    state = (
        "Run completion unavailable"
        if metrics is None
        else "Run complete"
        if complete
        else "Partial run"
    )
    if complete and metrics and (summary.operational_errors or summary.error or metrics.run_error):
        state = "Run completed with errors"
    paid = sum(
        (r.payment.amount_usd or Decimal(0) for r in result.results if r.payment.status == "paid"),
        Decimal(0),
    )
    cards = metric(
        "New mock payments", _money(paid), f"{summary.new_payments} payments recorded in this run"
    )
    if metrics:
        cost = (
            "Unavailable"
            if metrics.estimated_api_cost_usd is None
            else f"${metrics.estimated_api_cost_usd:,.6f} USD"
        )
        cards += metric(
            "Estimated API spend",
            cost,
            "Complete cost accounting"
            if metrics.cost_complete
            else "Incomplete cost accounting · known cost is a lower bound",
        )
        cards += metric(
            "Tokens used",
            f"{metrics.total_tokens:,}",
            "Complete usage" if metrics.usage_complete else "Incomplete usage · known tokens only",
        )
        cards += metric(
            "Blocked exposure",
            _money(metrics.blocked_payment_exposure_usd),
            "Unpaid rejected invoices · not realized savings",
        )
        usage = f"""<dl><dt>Input / output tokens</dt><dd>{metrics.prompt_tokens:,} / {metrics.completion_tokens:,}</dd>
<dt>Cached / reasoning tokens</dt><dd>{metrics.cached_prompt_tokens:,} / {metrics.reasoning_tokens:,} (subsets of input / output)</dd>
<dt>Model calls / attempts</dt><dd>{metrics.model_calls} / {metrics.transport_attempts}</dd>
<dt>Cost basis</dt><dd>{text(metrics.cost_basis)}</dd><dt>Elapsed time</dt><dd>{metrics.wall_ms / 1000:.2f}s</dd>
<dt>Operational error rate</dt><dd>{metrics.operational_error_rate:.1%}</dd>
<dt>Exposure coverage</dt><dd>{metrics.exposure_invoice_count} valued invoices; {metrics.exposure_unvalued_count} without a usable amount</dd></dl>
<details><summary>Timing and metric details</summary>{raw(metrics.model_dump(mode="json"))}</details>"""
    else:
        cards += metric("Estimated API spend", "Unavailable", "No metrics recorded")
        cards += metric("Tokens used", "Unavailable", "No metrics recorded")
        cards += metric("Blocked exposure", "Unavailable", "No metrics recorded")
        usage = "<p>Usage and cost metrics unavailable.</p>"
    notice = ""
    if metrics is not None and not complete:
        notice += '<p class="banner">Partial run: only recorded outcomes are shown. Remaining invoices have no final decision. Reconcile the ledger before retrying.</p>'
    if summary.error:
        notice += f'<p class="banner">{text(summary.error.code)}: {text(summary.error.message)}</p>'
    batch_events = [e for e in result.trace if e.source_id is None]
    # Include source events without a terminal result, especially after interruption.
    recorded_sources = {item.source_id for item in result.results}
    unfinished = [
        e for e in result.trace if e.source_id is not None and e.source_id not in recorded_sources
    ]
    unfinished_view = ""
    for source_id in dict.fromkeys(e.source_id for e in unfinished):
        source_events = [e for e in unfinished if e.source_id == source_id]
        unfinished_view += f"<details><summary>Unfinished source · {text(source_id)}</summary>{vp_review(None, source_events)}{timeline(source_events)}</details>"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>Invoice processing report · {text(summary.run_id)}</title><style>{_CSS}</style></head><body><main>
<header><div class="eyebrow">Accounts payable / local simulation</div><h1>Invoice processing report</h1>
<p><b>{text(state)}</b> · {len(result.results)} of {summary.discovered} invoice outcomes recorded</p>
<small>Run <code>{text(summary.run_id)}</code></small></header>{notice}<section aria-label="Run totals"><div class="metrics">{cards}</div>
<div class="strip"><span><b>{summary.approved}</b> approved</span><span><b>{summary.rejected}</b> rejected</span><span><b>{summary.duplicate_skips}</b> already paid</span><span><b>{summary.operational_errors}</b> operational errors</span></div></section>
<h2>Invoice outcomes</h2><p class="muted">Expand each invoice to view agent steps, tool results and decision evidence. Step numbers follow the order of events across the run.</p>
{"".join(invoice(item, i) for i, item in enumerate(result.results, 1)) or "<p>No terminal invoice outcomes recorded.</p>"}{unfinished_view}
<h2>Token usage and run details</h2>{usage}
<details><summary>Run-level process · {len(batch_events)} events</summary>{timeline(batch_events)}</details>
<details><summary>Final inventory and skipped inputs</summary>{raw({"final_inventory": summary.final_inventory if summary.final_inventory else "Unavailable", "skipped_inputs": summary.skipped})}</details>
<footer>This report describes a local mock payment simulation. Approval and payment are separate outcomes. Financial amounts use the existing fixed mock FX policy. No real funds move.<br>
Run artifacts · <a href="results.jsonl">Results</a> · <a href="audit.jsonl">Audit</a> · <a href="trace.jsonl">Trace</a> · <a href="metrics.json">Metrics</a></footer>
</main></body></html>"""


def write_report(result: RunResult, path: Path, *, secrets: list[str] | None = None) -> None:
    """Publish a complete UTF-8 document atomically in its run directory."""
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            stream.write(render_report(result, secrets=secrets))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
