# Invoice agent delivery status

**Implementation and offline verification complete. Live xAI acceptance is blocked on the local API key.** The build remains in review until live validation runs; no provider success is claimed.

## Completed deliverables

| Work | Owner(s) | Verification |
|---|---|---|
| Architecture/contracts/configuration | Root, contracts | Typed models, explicit ports, Decimal safeguards, frozen review binding |
| Readers/normalization/ingestion | ingestion | Actual TXT/JSON/CSV/XML/PDF fixtures; bounded source-backed correction graph |
| xAI runtime/VP review | contracts, Root | HTTP adapter tests, JSON Schema/tool envelopes, real critique/revision graphs |
| Inventory validation/payment | build_setup, blind_review | Actual inventory tool graph, deterministic blockers, real SQLite atomicity |
| Batch/CLI/observability | Root | All-ingestion barrier, chronological stock consumption, JSONL, durable audit and partial outcomes |
| Evaluation | ingestion | 20 isolated sources + 3 stateful scenarios, all passing |
| Independent review | blind_review, contracts, ingestion, build_setup | Concrete defects reproduced, fixed, regression-tested; triage in build spec |
| Packaging/CI | Root | Source/wheel build succeeds; SQL included; offline CI configured |

## Verification evidence

- Full offline suite: **238 passed**, including the full-CLI subprocess HTTP/SQLite test.
- Branch-inclusive application coverage: **94%**. Critical modules: database 93%, validation 93%, graph 91%, identity/payment 100%.
- Ruff checks and formatting pass; mypy passes 21 application/CLI/evaluator files.
- Fixture evaluation: **23/23 cases pass** using reviewed extraction fixtures and scripted model responses, with actual readers, graphs and SQLite.
- Full supplied folder: **2 mock payments, USD 6,890.00**; final stock WidgetA 2, WidgetB 3, GadgetX 5, FakeItem 0.
- Actual standalone CLI subprocess exercised a local HTTP provider double through XAIClient, then tool calls, VP calls, mock payment and committed ledger. It verifies application integration, not live xAI behavior.
- Interruption regression proves earlier committed outcomes remain in results.jsonl/audit.jsonl and the run directory is reported.
- Live test attempt: failed at local Settings validation because XAI_API_KEY was missing, before any provider request. Two opt-in live tests are available, including clean payment and rejected 1002 paths.

## Remaining credential-dependent acceptance

Populate the ignored local `.env` from `.env_example`, then run:

```bash
RUN_LIVE_XAI=1 uv run pytest tests/live -q
uv run python scripts/evaluate.py --live
uv run python main.py --invoice_path=data/invoices --trace
```

The default is explicitly grok-4.3 because xAI documents grok-3 redirects to it. No model or provider fallback occurs silently. Credentials were never printed or committed.

No remote push/submission is performed while live acceptance is pending. Run databases, raw audit artifacts, .env, render snapshots and caches are ignored. Vendored BMAD helpers retain upstream provenance and license and are not application runtime dependencies.

## Review scope and limitations

The initial adversarial and integration reviews resolved lost fatal-error results, misleading commit events, missing tool recovery, and source under-extraction. A final fresh-context blind review plus edge and verification passes found 14 additional source/evidence/operator cases, all addressed. Thread limits required two final layers to reuse earlier agents; their prior authorship is disclosed in review records.

Known product scope: no OCR, real banking, external business services, automatic revision supersession, or cross-run duplicate protection. Source checks cannot prove complete semantic understanding of arbitrary text. Fresh-run payment repetition is deliberate user-directed simulation behavior.
