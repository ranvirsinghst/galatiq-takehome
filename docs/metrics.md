# Run reports and metrics

Run the usual command; no extra flag is needed:

```bash
uv run python main.py --invoice_path=data/invoices
```

High-level live progress remains on stderr. Stdout contains readable invoice outcomes, a compact final invoice table, and the run summary. Use `--json` for machine-readable stdout. Detailed traces never go to either terminal stream.

Each unique run directory contains:

| File | Contents |
|---|---|
| `trace.jsonl` | Sanitized events in sequence, flushed and synced as emitted, including usage and failed-call timing |
| `metrics.json` | Run-wide cost, usage, latency, error rates and potential exposure |
| `results.jsonl` | Completed invoice outcomes plus final summary and metrics |
| `audit.jsonl` | Source evidence, validation, and VP decisions for completed outcomes |
| `inventory.db` | This invocation's inventory and committed mock payment ledger |

`--trace` additionally embeds structured events in the results artifact. It does not enable terminal trace noise. Trace collection is always enabled. On interruption or run failure, metrics are marked `run_complete: false` and saved when storage is writable; retained trace events support inspection of unfinished work. A killed process or inaccessible disk can prevent final metrics from being written. A trace write failure is reported as a run error while preserving committed payment outcomes; a terminal reporting failure does not overwrite an already persisted complete summary. Partial reports are not complete-success summaries.

## Token usage and estimated spend

Token counters include all observed responses, including responses rejected by schema validation and subsequent correction/review calls. They do not count repeated trace representations twice. Completion tokens are normalized to billed output including reasoning; reasoning and cached input are reported separately as subsets, not additional tokens to add to the total. xAI's Chat Completions and Responses usage shapes differ, so totals determine whether separately reported reasoning must be included.

Cost first uses the provider's `cost_in_usd_ticks` per response, converted with exact Decimal arithmetic: **USD = ticks / 10,000,000,000**. xAI documents that this value includes applicable caching discounts. [Provider cost tracking](https://docs.x.ai/developers/cost-tracking).

When provider cost is absent, the fallback supports exact model `grok-4.3` using a **2026-09-08 pricing snapshot**: USD 1.25 per million uncached input tokens, USD 0.20 per million cached input tokens, and USD 2.50 per million output tokens. All rates double when a request's prompt reaches 200,000 tokens. The threshold applies per request, not to run totals. When cached-input detail is absent, fallback pricing assumes uncached input. Unknown models are not silently priced as Grok 4.3. [Official pricing](https://docs.x.ai/developers/pricing).

`cost_basis` records which source was used. Missing counts, incoherent usage, or transport attempts without reported usage are disclosed through `usage_complete` and `cost_complete`. An available partial spend is a known subtotal, not a promise that unobserved attempts were free. An entirely unknown spend is `null`, displayed as unavailable. Estimates exclude local compute and developer labor. The saved pricing snapshot may differ from future rates; provider-reported costs take precedence.

## Latency

`wall_ms` measures elapsed run time through processing and final inventory retrieval, before final report serialization. `agent_latency_ms` groups full stage duration by ingestion, validation, approval, and payment, including local work and model calls. `model_latency_ms` separately groups only model-call time by ingestion, inventory, VP proposal, critique, and revision. Each model call includes its transport retries and backoff, including failed calls. `model_calls` counts semantic calls; `transport_attempts` counts actual attempted HTTP requests. These values identify expensive stages without confusing one logical call with its retries. Stages skipped by deterministic rejection (such as VP approval) have no latency entry.

## Error rates

`operational_error_rate` = invoice outcomes with operational errors / supported invoices discovered. `rejection_rate` = business rejections / supported invoices discovered. Rejection for insufficient stock is expected business behavior, not a processing error. A run-level failure such as unavailable final inventory is separately flagged by `run_error`; it does not invent an invoice failure. Partial reports retain the discovered-input denominator and are explicitly labeled incomplete. These rates do not measure extraction accuracy or false positives, which require ground truth.

## Potential loss avoided

The displayed value is **blocked payment exposure**, not proven fraud prevention or realized financial savings. It assumes an otherwise-unchecked payment could have released the stated invoice amount.

Count positive, finite parsed USD amounts on business-rejected invoices. Group by normalized vendor and invoice number, taking the maximum rejected amount per identity rather than summing repeated formats or versions. Exclude identities that were paid or already paid anywhere in this invocation. Without identity, fall back to source hash, then source ID. Unknown/nonpositive amounts are excluded and counted in `exposure_unvalued_count`; operational failures are excluded. This is an estimate from the supplied documents, not an audited loss figure or a claim about the assignment's USD 2M annual baseline.
