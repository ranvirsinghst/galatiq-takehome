# Independent integration review

Scope: source-reader/ingestion boundaries, real provider-adapter/tool-loop composition, fatal operator failures, chronological batch behavior, and supplied fixture expectations. Financial evidence validation was reviewed separately.

## Findings and resolutions

| Priority | Finding | Resolution and regression evidence |
|---|---|---|
| P1 | An unusable shared database raised again during the final inventory snapshot, losing accumulated invoice and unprocessed results. | Runner preserves results and reports a summary error with unavailable inventory. `test_fatal_storage_retains_invoice_results` passes with a deliberately unusable real SQLite store subclass. |
| P1 | Directory permission errors escaped CLI discovery's error handler. | CLI returns sanitized structured failure. `test_cli_directory_permission_failure_is_structured` verifies no sensitive exception details or stdout output. |
| P2 | Actual provider adapter reports malformed/unknown tool responses as `LLM_SCHEMA_ERROR`, while the tool graph only retried direct scripted malformed responses. | Tool graph now consumes schema errors inside its existing two-round budget. `test_real_adapter_invalid_tool_response_recovers_within_graph` uses real XAIClient parsing, httpx MockTransport, LangGraph, and SQLite; first unknown tool recovers on the second real adapter response. |
| P2 | Both SQLite store and processing graph emitted `payment_committed` for one transaction. | Store is the sole committed-event owner. `test_one_commit_event_per_new_payment` verifies exactly one event. |

All identified findings above are resolved. No unresolved integration blocker was found in the reviewed offline paths.

## Verification

- `uv run pytest tests/integration/test_failure_paths.py -q`: **7 passed**. Includes source disappearance with healthy-file continuation, missing-date rejection before sorting, and fatal provider stopping subsequent model work.
- `uv run python scripts/evaluate.py --suite all`: **23/23 cases passed**: 20 supplied sources in isolated runs and three stateful scenarios.
- Stateful full folder: two new payments, USD 6,890.00, stock WidgetA 2 / WidgetB 3 / GadgetX 5 / FakeItem 0.
- Equivalent PDF/TXT scenario produces one new payment plus one duplicate skip; revised paid identity produces `VERSION_CONFLICT`.
- Production modules D/E pass Ruff and mypy. Their focused deterministic/tool/real-SQLite suite passed 44 tests before the additional cross-boundary tests.

## Limits

Offline evaluation uses independently recorded extraction fixtures and scripted model responses; actual readers, extraction checks, graph routing, validation, payment transactions and result serialization execute. This verifies integration and policy, not live model reliability. Live xAI verification remains a separate credential-dependent gate. The outermost unexpected-error handler deliberately reports sanitized failure rather than claiming success.
