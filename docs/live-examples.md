# Live invoice-agent examples

Verified on 2026-09-08 using the configured xAI Grok 4.3 endpoint and the local `.env`. Model calls are real; payments are the intended local mock, committed to SQLite. Each command starts with fresh inventory and ledger.

## Single invoice: approved payment

```bash
uv run python main.py --invoice_path=data/invoices/invoice_1001.txt --trace
```

Observed: INV-1001 approved, USD 5,000.00 paid. WidgetA stock falls from 15 to 5; WidgetB from 10 to 5. The database contains one payment for INV-1001. The mismatched Net 15 due date is retained as a warning and acknowledged by the VP.

Local evidence: `runs/4ce568f47dfa4e9cb05ead45b35b855f/` (`results.jsonl`, `audit.jsonl`, `inventory.db`).

## Single invoice: rejected stock overrun

```bash
uv run python main.py --invoice_path=data/invoices/invoice_1002.txt --trace
```

Observed: INV-1002 rejected, USD 0 paid. Extraction reads GadgetX quantity 20 and payment terms Net 30 independently. Inventory validation reports `INSUFFICIENT_STOCK`: 20 required, 5 available. `TERMS_DATE_MISMATCH` reports the stated January 30 due date against the March 1 date implied by Net 30. SQLite contains no payments and stock is unchanged.

Local evidence: `runs/c1876e81b6274e93a4c64fa7378fa228/`.

## Stateful folder

```bash
uv run python main.py --invoice_path=data/invoices --trace
```

All supported files are ingested before any payment processing. Processing then follows invoice date with deterministic tie breaking. Results are streamed as each invoice completes; the final JSON line is the run summary.

Observed: 20 completed, 2 approved and paid, 18 rejected, zero operational errors. Payments: INV-1001 USD 5,000.00 and INV-1004 USD 1,890.00; total USD 6,890.00. Final inventory: WidgetA 2, WidgetB 3, GadgetX 5, FakeItem 0. The audit confirms all 20 ingestion terminal events precede processing; valid dates are sorted. SQLite payment count and stock match the summary.

Local evidence: `runs/fd69bc3b125045a48c94cb3c3fa770b5/`. This run took approximately 405 seconds, recorded 81 model responses, resolved to `grok-4.3`, and used zero transport retries.

## Validation

```bash
RUN_LIVE_XAI=1 uv run pytest tests/live -q
uv run python scripts/evaluate.py --live
```

The full live evaluator passed **23/23 cases** (20 isolated sources and 3 stateful scenarios). The two live tests passed, covering extraction, inventory tool use, VP proposal/critique, rejected stock overrun, and committed clean mock payment. The live run exposed a raw `0%` tax-rate parsing issue and a high-value rejection prompt ambiguity; both were corrected and regression-tested. Run IDs and model wording vary between invocations.
