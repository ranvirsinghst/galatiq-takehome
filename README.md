# Galatiq invoice agent

Process invoices with Python, LangGraph, xAI Grok, and SQLite. The agent extracts invoice details, checks inventory, totals and catalog prices, simulates VP approval, and records mock payments with an audit trail. **No real payments are made.**

## Business value

Automated checks target processing errors; batch processing reduces manual handoffs; readable decisions help reviewers resolve exceptions. Savings and turnaround improvements are not measured by this prototype.

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

Keep `.env` local. The default model is `grok-4.6`, as configured in [.env_example](.env_example); set `XAI_MODEL` to change it.

## Runtime

Grok calls require internet access. Inventory, reports, and mock payments run locally; SQLite is created and seeded automatically. Offline tests use simulated model responses.

## How it works

- Extracts vendor, amounts, items, quantities, and dates from TXT, JSON, CSV, XML, and text-based PDFs. Uses conservative item aliases and bounded extraction repairs; missing or invalid required data blocks payment. Scanned PDFs need OCR, outside scope.
- Ingests the folder first, then processes oldest first against shared stock. **Every invocation starts with fresh inventory and an empty ledger; duplicate protection applies within that run only.**
- Rejects invalid quantities, unknown items, insufficient stock, and inconsistent totals. Amounts use fixed mock USD exchange rates.
- Catalog prices above +10% block payment; below −10% require VP acknowledgment; exactly ±10% passes. To catch overcharges that arithmetic checks miss, we seeded the recurring USD unit prices in the sample invoices: WidgetA $250, WidgetB $500, and GadgetX $750. These demo references cannot establish contract prices or detect consistent inflation.
- Eligible invoices receive VP approval, critique, and bounded revision. Above $10,000, review must assess arithmetic, aggregate stock, data completeness, and suspicious signals. Deterministic checks guard payment.

Results, audit/trace logs, metrics, SQLite, and an HTML report are saved under `runs/<run_id>/`. The report shows VP explanations, revisions, timelines, estimated API spend, and blocked payment exposure—not realized savings. Use `--open-report` to view it, `--json` for structured stdout, `--trace` to embed events in results, or `--output_dir` to change the artifact root.

## Try these scenarios

Run each separately with `uv run python main.py --invoice_path=data/invoices/<file> --open-report`:

| File | Expected outcome |
| --- | --- |
| `invoice_1001.txt` | Approve and mock-pay $5,000 |
| `invoice_1002.txt` | Reject: insufficient GadgetX stock and payment-term/date mismatch |
| `invoice_1008.txt` | Reject: unknown items |
| `invoice_1010.txt` | Reject: WidgetA priced 20% above catalog |

Folder results differ because successful payments consume shared stock.

## Test

```bash
# Offline; no API key needed
uv run pytest tests/unit tests/integration -q
uv run python scripts/evaluate.py

# Live; requires your xAI key
RUN_LIVE_XAI=1 uv run pytest tests/live -q
uv run python scripts/evaluate.py --live
```

Verified fixture evaluation: **23/23 cases passed offline and live**, covering individual invoices, shared inventory, duplicates, and revisions. Live outcomes depend on provider responses.

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
