# Verification and completion standard

Use real normalized candidates, LangGraph routing, SQLite transactions, and CLI serialization. Script only the model boundary for offline tests. Do not replace the validator or payment store with success-returning mocks in integration tests. Keep failures distinguishable from ordinary invoice rejections.

## Unit coverage

1. Schema/seed provides exactly the three reference prices, recreated identically each invocation. Lookup returns typed decimal data and explicit missing rows; invalid stored prices fail clearly.
2. Price rules cover every row in implementation.md's example table, both exact boundaries, just inside/outside each boundary, and existing FX rounding. Assert codes, severity, line IDs, observed/reference amounts, and retained notes.
3. Mixed/repeated lines cannot average away an overcharge. Quantity aggregation remains correct. Taxes/shipping do not affect unit-price comparison.
4. Missing source unit price blocks; invalid quantity/amount/currency retains existing source blockers without exceptions. Missing/invalid catalog data for eligible items is an operational error. Unknown and zero-stock items retain normal rejection without needing prices.
5. Complete pricing evidence is required for eligible reports. Candidate/FX tampering cannot produce a valid report. Older saved results still render without fabricated pricing history.
6. Tool protocol covers both tools in one response, prices first, split/incremental coverage, null vs omitted rows, unknown tools, extra arguments, invalid item names, duplicate IDs, missing calls, cross-run evidence, and bounded exhaustion. No call executes if another call in that response is malformed. Assert real store reads and actual returned values.
7. Price warnings require VP acknowledgment; refusal or endless critique remains bounded. Price blockers cannot be waived by scripted approve responses, even above the high-value threshold.

## Integration coverage

- Isolated invoice 1010: retain total USD7185.00; reject with PRICE_OVERCHARGE for WidgetA $300 versus $250; preserve “rush order”; zero VP and mock-payment calls; empty payment ledger; unchanged stock.
- Isolated 1001 and 1014: pay with genuine inventory and catalog tool evidence. Verify ledger amounts and expected decrements. The EUR +4.5% line is inside tolerance.
- Synthetic payable invoice with price below −10%: warning reaches proposal/critique; acknowledgment permits payment. Exact −10% needs no warning. Keep quantities within stock and totals correct to isolate pricing behavior.
- Already-invalid invoices: source/stock rejection does not become an operational pricing error. Invoice 1002 still rejects without VP; unknown/zero-stock items require no catalog entry.
- Payment bypass: direct `store.pay` with missing catalog evidence, stripped price findings, fabricated reference, changed candidate, wrong run identity, or catalog changed after review never calls the mock or writes stock/ledger.
- Inject store/tool faults and mock-payment failure. Assert rollback, structured operational errors, and persisted failure evidence. Successful payments still commit ledger and stock together.
- Stateful folder: all ingestion completes before payment; oldest invoices consume stock; rejected overcharges do not consume stock, allowing a later legitimate invoice to use it. Duplicates and changed paid versions behave as before. A second invocation starts fresh.
- CLI subprocess: readable reason and final summary, saved report with catalog evidence, valid `--json`, per-run trace with price tool events, correct metrics and exit codes. Business rejection exits 0; catalog/tool operational failure exits 1. Preserve partial reports on interruption/failure where storage permits.

## Fixtures and evaluator

Update scripted provider doubles to answer both tool schemas. Do not hardcode approved outcomes to compensate for incomplete evidence.

In `tests/fixtures/expected/outcomes.json`, change only invoice 1010's isolated expected decision to `rejected`, adding `PRICE_OVERCHARGE`; retain its total. The other 19 isolated outcomes and existing required finding codes remain unchanged. Existing stateful expectations must be verified against date ordering and actual stock, not mechanically copied from isolated outcomes. Document any justified stateful change before changing its assertion.

## Required commands

Run from the repository root after implementation:

```bash
uv run pytest tests/unit tests/integration -q
uv run pytest tests/unit tests/integration --cov=invoice_agent --cov-branch --cov-report=term-missing
uv run ruff check invoice_agent main.py tests scripts
uv run ruff format --check invoice_agent main.py tests scripts
uv run mypy invoice_agent main.py
uv run python scripts/evaluate.py
```

Inspect uncovered new pricing branches; cover meaningful error and boundary paths rather than chasing a percentage with trivial tests. Fix failures caused by the change; document any independently established pre-existing failure.

With the local xAI credentials, verify real tool use and the final payment path:

```bash
RUN_LIVE_XAI=1 uv run pytest tests/live -q
uv run python main.py --invoice_path=data/invoices/invoice_1010.txt --trace
uv run python main.py --invoice_path=data/invoices/invoice_1001.txt --trace
uv run python main.py --invoice_path=data/invoices/invoice_1014.xml --trace
uv run python main.py --invoice_path=data/invoices --trace
uv run python scripts/evaluate.py --live
```

Each command starts fresh state. Inspect traces for actual lookup_price calls on eligible candidates and 1010, inspect the generated HTML report, and query run SQLite files to verify ledger/stock outcomes. A successful process exit alone is insufficient. Do not print credentials. If credentials are unavailable, finish offline work and report live validation as blocked, never passed.

## Deliverable

Provide implementation, regression tests, fixture updates, and updated documentation. Record commands/results, run artifact paths, and any remaining limitations in `docs/build/status.md`. Review specifically for price-evidence bypasses, incorrect tolerance boundaries, and changed failure classification before declaring completion. The final summary must distinguish verified offline behavior from live observations.
