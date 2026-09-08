# G — Batch coordinator, processing graph, CLI, and observability

## Boundary

Own `runner.py`, processing `graph.py`, `output.py`, `main.py`, application integration tests, and user-facing README additions. Compose B ingestion, D validation, F VP review, E storage/payment, and C runtime through A's contracts. Keep main.py thin: argument parsing, configuration, dependency wiring, exit status.

The coordinator is the integration owner. It must not duplicate module business logic to work around incomplete implementations.

## Discovery and run lifecycle

- `--invoice_path` accepts one supported file or a non-recursive directory.
- Discover supported files in stable path order; list unsupported directory entries as skipped.
- Invalid input path, explicit unsupported file, or no supported inputs is exit 2.
- Create a unique run ID/database for each invocation. Same output folder is allowed, but never shares ledger/inventory with old runs.
- Ingest every file to a terminal ingestion outcome before any processing-time inventory lookup, review, or payment.
- Freeze candidates after correction. Sort valid-date candidates by invoice date, filename, then relative path. Invalid/missing date produces terminal rejection, no guessed position.
- Process sequentially. Check paid identity before depleted-stock validation so duplicates return already_paid rather than a new stock rejection.
- Invalid source candidates with valid dates may proceed to rejection review with their findings; never construct a payable request until all requirements pass.
- Preserve results for terminal ingestion failures and errors. Every supported discovered file has exactly one result.

Source errors isolated to one file do not stop healthy files. Permanent shared authentication failure or unusable SQLite state stops dependent work; produce explicit error outcomes for remaining files rather than silently dropping them. Do not perform pointless repeated failing calls for every remaining invoice.

## Processing graph

Compose identity check -> validation tool loop -> deterministic validation -> VP review subgraph -> final gate -> payment or rejection. Conditional edges must be explicit and testable. Domain rejection routes terminate normally. Operational exceptions are mapped once at the orchestration boundary using the shared taxonomy.

Final gate verifies accepted approval, complete evidence, no blocker, required VP checklist/acknowledgments, and candidate/report digest consistency. E performs transaction-local identity/stock checks again.

A late extraction error produces `LATE_EXTRACTION_ERROR` and rejection. Do not modify a frozen date, return to ingestion, or reorder after payments have started. This is a deliberate MVP limitation; all normal correction occurs before the barrier.

## Output

Default stdout is JSONL: one typed invoice result per supported input and a final summary. Results for processable invoices follow processing order; emit unsortable terminal ingestion results in stable source-path order before processing results. The timing of result printing does not permit payment before the barrier.

Summary fields include supported inputs, skipped paths, completed, approved, rejected, errors, new payments, duplicate skips, newly paid USD, and final stock. Totals must reconcile: approved includes already_paid, while new payments and paid USD exclude it. Error is not a business decision; rejected input is not an operational failure.

Progress/diagnostics use stderr. No lower-level module prints. Without trace, result reasons and material findings remain visible. With `--trace`, include structured event details or a clearly identified trace artifact consistently documented in README. Prefer a trace array per result plus run events in the summary for a simple JSONL implementation.

Trace event schema comes from A. Include source/run IDs, processing index when assigned, monotonic sequence, stage/phase/attempt, duration, codes, inventory generation, and concise evidence/reasons. A paid event means database commit completed, never merely that the mock returned success.

Do not log credentials, complete prompts, hidden reasoning, or raw provider exception bodies. Redact at the centralized serializer as defense in depth; minimize sensitive fields before emission.

Exit 0 for completed processing including business rejections, 1 for operational errors, 2 for invalid CLI/config. Handle keyboard interruption with a clear nonzero exit and transaction cleanup; never print a successful complete summary on interruption.

## Integration tests

Use actual coordinator, both graphs, readers, real SQLite, output serializer, and scripted LLM port:

- Event ordering: every ingest terminal event precedes first lookup/review/payment.
- Corrected date determines order; filename conflict confirms date priority; equal dates use stable tie-breaker.
- Earlier successful payment reduces later availability; earlier rejection does not reserve stock.
- Folder and single-file invocations both start fresh; two calls sharing output root remain isolated.
- Duplicate-before-stock behavior and paid version conflict.
- One malformed input still yields results for all others; shared fatal error marks remaining files explicitly.
- Late extraction error rejects without reordering or mutation.
- Every file produces exactly one result and all summary equations hold.
- Trace off/on preserves business outcome, output JSONL validity, reasons, and redaction.
- Commit failure emits no paid event and produces exit 1.

Test the CLI with argument parsing and captured streams; add at least one subprocess smoke using a test-only dependency harness or injected launcher. Do not expose a production fake-model switch just to run tests. Test fixture harnesses belong under tests, not application entry-point conditionals.

## Documentation exit gate

README keeps the assignment context and adds working setup, `.env_example`, tested model/client, file/folder commands, reset semantics, date ordering/ties, mock FX, duplicate/version behavior, trace example, evaluation commands, and limitations. Clearly state that a new invocation can simulate paying the same invoice again.
