# Independent runtime and payment review

Reviewed the implemented ingestion, normalization boundaries, validation, identity, database/payment, VP graph, runner, output, CLI, provider adapter, and associated tests against the adopted module specifications.

## Findings and disposition

1. **High priority: table quantities could be silently understated.** `_source_issues()` used substring matching for quantity evidence. A response extracting `2` from `WidgetA 20 10.00 200.00` passed; table rows were counted but their quantity columns were not independently reconciled. The explicit `Qty`/`xN` equality branch did not cover tables. Reproduced with `tests/integration/test_adversarial.py::test_table_quantity_misread_must_be_flagged`; sent to ingestion owner for correction. This matters because source-backed validation must reject an extraction error before stock consumption.
2. **Operational resilience: final stock lookup could discard accumulated results.** `runner.run()` queried the final snapshot outside its error handling. A fatal database failure could prevent emitting the already-collected per-source errors/results. Integration owner corrected this by retaining results and exposing a structured summary error with unavailable final inventory.
3. **Operational observability: unexpected outer exceptions lacked structured diagnostics.** The CLI initially caught only OSError and AgentError, while unexpected per-input exceptions were converted into generic errors without a cause-class trace event. Integration owner added sanitized exception-type trace diagnostics and an outer INTERNAL_ERROR boundary.

## Verified safeguards

- The database re-derives and compares the entire deterministic report before payment, so a forged report cannot remove a terms warning to avoid VP acknowledgment. The adversarial test proves the mock is never called.
- Mutating a nested candidate list after approval invalidates the full candidate digest. Frozen Pydantic boundaries alone do not deeply freeze lists; the digest check provides the necessary mutation detection.
- Runtime schema failures, transient transport failures, and permanent provider failures are distinct and bounded. No schema repair occurs secretly inside the HTTP adapter.
- VP critique is a separate actual graph call. Three schema-only failures consume three cycles; endless normal revision consumes six calls. Deterministic shared guards prevent approval over blockers and missing enhanced scrutiny.
- A hostile reviewer can still produce unsupported prose that another model accepts. Mechanically checking factual prose is explicitly outside the deterministic guarantee; such a rejection grants no payment authority. This limitation must remain documented rather than claimed solved by reflection.
- Real SQLite integration tests connect deterministic validation, the VP graph, and payment. Unknown items and negative quantities cannot create payment requests.

## Execution evidence

C/F unit verification after additional adversarial coverage: 35 tests passed; 98% statement coverage for the HTTP adapter and 96% for the VP graph (97% combined). Three real SQLite review integration tests also pass. One opt-in live test remains skipped. Ruff and mypy passed the runtime/review modules. The independent adversarial suite initially reproduced one failure and passed three safeguards. After the ingestion correction, all four adversarial tests pass.

Live xAI behavior remains unverified until a local key is available and the explicit live gate executes. A skipped test is not provider compatibility evidence.

Final review disposition: all reproduced regressions pass. No remaining known payment authorization bypass; live provider compatibility is the remaining external verification gate.
