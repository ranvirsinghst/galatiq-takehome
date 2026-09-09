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


## Catalog price validation follow-up

Implemented 2026-09-09 from `docs/specs/spec-catalog-price-validation/` and ADR-008. The catalog supplies corpus-derived USD references through real `lookup_price` calls, alongside inventory calls. Decimal comparisons reject above +10%, warn below −10%, and pass exact boundaries. Payment independently checks submitted catalog evidence against current SQLite facts before and inside the transaction. Missing or malformed eligible-item catalog data fails closed; source/stock blockers preserve business rejection. The CLI and HTML report retain price evidence and explicit VP-skipped reasons. Historical reports remain readable without asserting prior price checks.

Baseline: 354 unit/integration tests passed before implementation. Initial feature verification: 402 tests passed, 95% branch-enabled application coverage, Ruff lint/format and mypy passed. Offline evaluator passed 20 isolated and 3 stateful cases; only invoice 1010's isolated expectation changed, retaining USD7185.00. Logs are under `runs/catalog-price-verification/`. Independent review identified deferred catalog errors and incomplete tool-recovery message history. Both were fixed, with additional interaction/CLI coverage; final results follow below.

### Observed live CLI evidence

Live commands used the locally configured `grok-4.6` model and 60-second timeout; no credentials were printed. Each invocation starts with fresh SQLite state. Run artifact paths below are relative to the repository root.

| Command | Observed result | Artifact directory |
|---|---|---|
| `uv run python main.py --invoice_path=data/invoices/invoice_1010.txt --trace` | Exit 0; USD7185.00 retained; PRICE_OVERCHARGE for WidgetA USD300 vs USD250 (+20%); rush note retained; VP skipped; no payment | `runs/003f2516eef84fee8219cc5fdf910e63/` |
| `uv run python main.py --invoice_path=data/invoices/invoice_1001.txt --trace` | Exit 0; paid USD5000.00; stock A5/B5/GadgetX5/Fake0 | `runs/e7e5a5a9ce0f46da9968d80277270372/` |
| `uv run python main.py --invoice_path=data/invoices/invoice_1014.xml --trace` | Exit 0; paid USD4537.50; stock A11/B4/GadgetX5/Fake0 | `runs/147ef78c08184e218c3f77f595518c09/` |
| Same 1014 command plus `--json` | Exit 0; stdout parses as invoice and summary JSONL records; payment succeeds | `runs/80e3f03fdf704d33be57265a6803edda/` |

Inspected actual `lookup_price` requests/results and correlated call IDs in trace.jsonl, structured catalog evidence and performed checks in audit.jsonl, HTML catalog sections, and SQLite payments/inventory. 1010 has no VP or payment-committed events, an empty payment ledger, and unchanged A15/B10/GadgetX5/Fake0 stock. Both tools share one model response in the isolated cases; model-call totals remain 2 for 1010, 4 for 1001 and 3 for structured 1014. No duplicate token accounting was introduced. The earlier 1010 run `runs/e3456d1818e44d5ebb0e772d835415f7/` established the same payment outcome but exposed verbose note formatting; the later run verifies the concise reason and explicit CLI VP-skipped message.

Initial `RUN_LIVE_XAI=1 uv run pytest tests/live -q` and its retry each produced 1 pass / 1 PROVIDER_TRANSIENT failure. The live test harness used the client's 30-second default instead of the locally configured 60-second timeout already honored by main.py and the evaluator. Both original failed logs are retained as `live-tests.log` and `live-tests-retry.log`; the harness now follows configured timeout, with subsequent results recorded below.

Browser visual inspection is unavailable: Browser runtime returned “No browser is available” and discovered no browsers. HTML artifact content and renderer regressions were inspected; no rendered-browser visual verification is claimed. Catalog references are corpus-derived, the 10% band is a demo policy, within-band or consistently inflated prices remain undetected, and vendor contracts are not verified.


### Final offline checks and review

| Command | Actual result | Log under `runs/catalog-price-verification/` |
|---|---|---|
| `uv run pytest tests/unit tests/integration -q` | 426 passed in 8.82s | `final-tests.log` |
| `uv run pytest tests/unit tests/integration --cov=invoice_agent --cov-branch --cov-report=term-missing` | 426 passed in 11.00s; 95% total coverage | `final-coverage.log` |
| `uv run ruff check invoice_agent main.py tests scripts` | Passed | `final-ruff-check.log` |
| `uv run ruff format --check invoice_agent main.py tests scripts` | 58 files formatted | `final-ruff-format.log` |
| `uv run mypy invoice_agent main.py` | Passed, 23 source files | `final-mypy.log` |
| `uv run python scripts/evaluate.py` | Passed, 20 isolated and 3 stateful cases | `final-evaluator.json` |
| `RUN_LIVE_XAI=1 uv run pytest tests/live -q` with configured timeout honored | 2 passed in 150.81s | `live-tests-configured-timeout.log` |
| `uv run python main.py --invoice_path=data/invoices --trace` | Exit 0, 20 completed, 2 payments totaling USD6890.00, 18 rejections, no errors | `folder.log`; artifacts `runs/08956838cda44b718f6a6012c7dcbf3e/` |

The full-folder SQLite ledger contains INV-1001 USD5000.00 and INV-1004 USD1890.00, with final stock WidgetA 2, WidgetB 3, GadgetX 5, FakeItem 0. Trace sequence numbers prove all ingestion finished before processing began. All 17 rule-blocked invoices skipped VP review; the remaining rejection is a changed paid version. Folder 1010 retains PRICE_OVERCHARGE alongside its stock blockers. Stateful expectations did not change. Token totals reconcile exactly to model-response trace usage: final isolated 1010 5,342; 1001 17,237; 1014 18,496; full folder 103,400 across 35 model responses. These observations are not a latency or cost benchmark.

Three independent review layers found two recovery defects and verification gaps. Deferred catalog errors now wait for outstanding inventory evidence within the same two-round budget, so an existing business rejection remains a rejection. Invalid returned evidence discards the current incomplete tool exchange before retry, preserving valid provider message history. Added regression tests inspect real HTTP adapter payloads, zero-read preflight, omitted warning acknowledgment and bounded critique, high-value overcharge routing, multi-item coverage, custom tolerances, transaction errors and runner persistence, and CLI JSON/exit codes/metrics. All review findings are resolved; no product-scope findings are deferred. Remaining uncovered branches are listed in the coverage log; meaningful catalog fault, tolerance and authority paths are exercised.


Final `uv run python scripts/evaluate.py --live`: **exit 0, all 23 cases passed** (20 isolated sources and 3 stateful scenarios). The complete machine-readable results are retained at `runs/catalog-price-verification/evaluate-live.json`, with empty stderr in `evaluate-live.stderr`. This confirms invoice 1010 as the only isolated decision change and preserves all stateful expectations using actual xAI responses. The live evaluator uses temporary per-scenario databases; persistent CLI databases and traces listed above provide the independently inspected stock/ledger evidence. Required offline and live verification is complete. No historical run artifacts were rewritten.
