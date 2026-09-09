# Invoice agent delivery status

**Implementation, review, offline verification, and live xAI acceptance complete.** Both live tests and all 23 live evaluation cases pass.

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

- Full offline suite: **311 passed**, including the full-CLI subprocess HTTP/SQLite test.
- Previously measured branch-inclusive application coverage, before the reporting additions: **94%**. Critical modules: database 93%, validation 93%, graph 91%, identity/payment 100%.
- Ruff checks and formatting pass; mypy passes 23 application/CLI/evaluator files.
- Fixture evaluation: **23/23 cases pass** using reviewed extraction fixtures and scripted model responses, with actual readers, graphs and SQLite.
- Full supplied folder, verified with both offline doubles and live xAI: **20 completed, 18 rejected, zero operational errors, 2 mock payments, USD 6,890.00**; final stock WidgetA 2, WidgetB 3, GadgetX 5, FakeItem 0.
- Actual standalone CLI subprocess exercised a local HTTP provider double through XAIClient, then tool calls, VP calls, mock payment and committed ledger. It verifies application integration, not live xAI behavior.
- Interruption regression proves earlier committed outcomes remain in results.jsonl/audit.jsonl and the run directory is reported.
- Live xAI acceptance: **2 passed in 71.71 seconds**, covering clean committed mock payment and rejected invoice 1002 with extraction, inventory tools, and VP reflection.
- Initial live examples exposed percentage tax-rate parsing and a high-value rejection prompt inconsistency; both fixed with 21 added regression cases.

## Live validation

Credentials are configured in ignored local `.env`. Reproduce the checks with:

```bash
RUN_LIVE_XAI=1 uv run pytest tests/live -q
uv run python scripts/evaluate.py --live
uv run python main.py --invoice_path=data/invoices --trace
```

The default is explicitly grok-4.3 because xAI documents grok-3 redirects to it. No model or provider fallback occurs silently. Credentials were never printed or committed.

Changes are committed locally; no remote push is performed. Run databases, raw audit artifacts, .env, render snapshots and caches are ignored. Vendored BMAD helpers retain upstream provenance and license and are not application runtime dependencies.

## Review scope and limitations

The initial adversarial and integration reviews resolved lost fatal-error results, misleading commit events, missing tool recovery, and source under-extraction. A final fresh-context blind review plus edge and verification passes found 14 additional source/evidence/operator cases, all addressed. Thread limits required two final layers to reuse earlier agents; their prior authorship is disclosed in review records.

Known product scope: no OCR, real banking, external business services, automatic revision supersession, or cross-run duplicate protection. Source checks cannot prove complete semantic understanding of arbitrary text. Fresh-run payment repetition is deliberate user-directed simulation behavior.

Final live evaluation: **23/23 cases pass** using real xAI, covering 20 isolated sources and 3 stateful scenarios. Reproducible commands and observed evidence are recorded in [Live examples](../live-examples.md).


## Run reporting follow-up

Live progress remains concise; detailed events flush to trace.jsonl per run. Final output includes a compact invoice table and metrics.json provides token/cost, full-stage and model-phase latency, operational error rate, and deduplicated blocked-payment exposure. Exposure is potential, not realized savings. Post-commit trace failure and console failure regressions preserve accurate payment/report state.

Final two-file live verification (`runs/330e5163da49462aba073c7519d7988e/`): 1 payment, 1 rejection, 19,331 tokens, USD 0.02815790 provider-reported cost (USD 0.028158 displayed), approximately 49.6 seconds, USD 15,000 blocked exposure. Metrics were reconciled to trace usage events; detailed trace never appeared on stdout/stderr. Offline evaluation remains 23/23. Definitions and pricing provenance: [Run metrics](../metrics.md).


## Combined review follow-up

The main processing graph now has identity, review, and payment nodes. The review node retains actual inventory tool calls and deterministic validation. Known blockers reject before VP model calls, including high-value invoices; eligible invoices retain proposal/critique/revision. The pure validator and final payment authority remain separate. Reasons: [ADR-007](../decisions/README.md#adr-007-one-review-node-with-early-deterministic-rejection).

- 311 unit/integration tests pass; both live xAI tests pass (33.39 seconds); offline evaluation 23/23. Ruff lint/format and mypy pass.
- Real two-file run: same 1001 payment and 1002 rejection, 6 calls versus earlier 8; observed 30.4 seconds versus 49.6 seconds. New run `runs/1c4b20e906894235863eaa765c6b6a88/`.
- Real full-folder run `runs/2ac9720f4eee4a04960af1c1344283a1/`: 20 completed, 2 payments totaling USD 6,890, 18 rejected, no operational errors; final stock A2/B3/G5/Fake0. SQLite verified. All 17 rule-blocked invoices have no VP review/calls; the remaining rejection is a changed paid version.
- This full-folder run recorded 40 model calls/responses, 78,729 tokens, USD 0.11590070 provider-reported spend, and 213.7 seconds. The earlier full-folder observation recorded 81 model responses and approximately 405 seconds. These are observed runs, not a controlled benchmark; model outputs, cache state and service timing vary. Regression tests establish the architectural call savings for known blockers independently of timing.
- Independent review found one documentation metadata placement error, fixed and verified by YAML parsing. No additional functionality or payment-safety regression found; no deferred findings.
