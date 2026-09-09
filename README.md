# Galatiq invoice agent

Process invoices with Python, LangGraph, xAI Grok, and SQLite. The agent extracts invoice details, checks inventory, totals and catalog prices, simulates VP approval, and records mock payments with an audit trail. **No real payments are made.**

## Quick start

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
cp .env_example .env
# Add your XAI_API_KEY to .env.

# Process one invoice
uv run python main.py --invoice_path=data/invoices/invoice_1001.txt

# Process a folder and open the results in your browser
uv run python main.py --invoice_path=data/invoices --open-report
```

Keep `.env` local. The default model is `grok-4.3`, as configured in [.env_example](.env_example); set `XAI_MODEL` to change it.

## How it works

- Reads TXT, JSON, CSV, XML, and text-based PDF invoices. Scanned PDFs require OCR, which is outside this prototype's scope.
- Ingests the entire folder first, then reviews invoices oldest first. Successful payments decrement stock. **Every invocation starts with fresh inventory and a fresh payment ledger.** Duplicate-payment protection applies within a run only.
- Rejects invalid quantities, unknown items, insufficient stock, and inconsistent totals with reasons. Eligible invoices proceed to VP approval, critique, and bounded revision; deterministic checks guard payment.
- Stores amounts in USD using fixed mock exchange rates.
- Looks up reference prices through a typed catalog tool. Unit prices more than 10% above the reference block payment; prices more than 10% below it require VP warning acknowledgment. Exactly ±10% passes.

The catalog is derived from the supplied invoice corpus, with a 10% demo tolerance band. Overcharges within that band, or prices inflated consistently across the corpus, remain undetected. This does not verify vendor contracts.

The terminal shows progress, outcomes, rejection reasons, and a final summary. Each run saves an HTML report, invoice results, audit and trace logs, metrics, and its SQLite database under `runs/<run_id>/`.

Each invoice has an expanded **VP review** section with the initial recommendation, the critique's explanation, any revisions, and the final review outcome. Explanations come directly from the VP responses; deterministic safeguards and inventory facts are labeled separately. Older runs explicitly identify explanations that were not recorded.

The report includes invoice timelines, estimated API spend, token usage, latency, and blocked payment exposure—not realized savings. Use `--json` for machine-readable stdout, `--trace` to also embed detailed events in invoice results, or `--output_dir` to change the artifact root. Detailed trace logs are always saved per run.

## Test

```bash
# Offline tests and fixture evaluation; no API key needed
uv run pytest tests/unit tests/integration -q
uv run python scripts/evaluate.py

# Live API tests; requires your xAI key
RUN_LIVE_XAI=1 uv run pytest tests/live -q
```

## Key Decisions

### Agent framework

- **Decision:** Use LangGraph for the agent workflow.
- **Rationale:** Explicit states and branches make retries, rejection paths, and review loops easy to follow and test.
- **Tradeoffs:** Adds one dependency and the complexity of handling a graph. Plain Python may be appropriate for a system of this size.

### Agent organization

- **Decision:** Ingest invoices first, then use separate graph nodes for identity checks, tool-backed validation, VP review, and payment. The VP critiques its own proposal in a separate call.
- **Rationale:** Pass structured invoice and validation data between stages. Deterministic blockers stop processing before VP review, and payment independently checks the approval evidence.
- **Tradeoffs:** Processing can require several model calls. The critique phase adds latency, and the same model might repeat mistakes.

### Deterministic payment checks

- **Decision:** Enforce payment rules in code. Reject invalid quantities, unknown items, stock overruns, inconsistent totals, and above-band catalog prices before reaching the VP review stage. Independently verify catalog evidence and current stock at payment and commit the ledger and inventory changes together.
- **Rationale:** These rules need repeatable answers. An LLM approval cannot waive a payment blocker.
- **Tradeoffs:** Rules need explicit maintenance and only catch problems we've defined. They don't prove the extracted facts are correct or the vendor is legitimate.

### Fresh state per run

- **Decision:** Start each invocation with seeded inventory and an empty payment ledger. Within a folder, ingest every invoice first, then process oldest first against shared stock.
- **Rationale:** Keep demos and tests repeatable while letting invoices compete for available inventory in date order.
- **Tradeoffs:** The same invoice can be paid again in a new run. Duplicate protection only applies within a run, and folder processing waits for ingestion to finish before making payments.

### Terminal output

- **Decision:** Show progress on stderr and readable outcomes on stdout. Save detailed traces per run; offer `--json` for scripts.
- **Rationale:** Users need to know what's happening and why an invoice was rejected. Raw event JSON made that hard to see.
- **Tradeoffs:** To avoid overwhelming users, the terminal omits plenty of detail. Debugging requires opening the saved artifacts.

### HTML report

- **Decision:** Save a self-contained HTML report for every run, with invoice outcomes, VP review, timelines, and metrics.
- **Rationale:** Give users (especially non-technical ones) a place to inspect decisions in a highly accessible format.
- **Tradeoffs:** It's a snapshot after processing, with no live updates, invoice editing, or approval controls.

## Further reading

- [Processing workflow](invoice_agent/graph.py) and [VP review](invoice_agent/approval.py)
- [Validation rules](invoice_agent/validation.py) and [payment persistence](invoice_agent/database.py)
- [Metric calculations](invoice_agent/metrics.py)
- [Fixture evaluator](scripts/evaluate.py) and [automated checks](.github/workflows/ci.yml)
