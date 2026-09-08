# Galatiq invoice agent — implementation specification

Status: build-ready specification, 2026-09-07.

> Parallel implementation entry point: [module specification package](specs/spec-invoice-agent/SPEC.md), including shared contracts, module ownership, and rigorous test gates. The package refines implementation boundaries and error/retry semantics while preserving the business decisions below.

Audience: agents implementing and reviewing the five-hour take-home prototype.

This document supersedes the architecture draft in `galatiq-invoice-agent-handoff.md`. The repository `README.md` remains the assignment brief. Explicit user decisions captured here take precedence over earlier proposals. Items marked **default** are implementation choices proposed to close ambiguity, not additional user requirements.

## 1. Objective and delivery boundary

Build a working local Python multi-agent invoice processor using LangGraph and xAI Grok. Demonstrate ingestion, inventory validation, automated VP approval with reflection, and local mock payment. Deliver runnable code, tests, setup instructions, and usable CLI output in a public GitHub repository. No deployment or web UI is required.

The prototype should make its decisions explainable through structured evidence and concise rationales. It should demonstrate actual LLM tool calls, typed outputs, bounded self-correction, and deterministic enforcement of payment policy.

Five-hour scope:

- Support provided TXT, JSON, CSV, XML, and text-based PDF fixtures.
- Use local SQLite for inventory and a payment ledger scoped to one invocation.
- Support one input file or a directory of files through `--invoice_path`.
- Ingest every file before processing any payment in a directory run.
- Process extracted invoices in ascending invoice-date order, consuming stock within that run.
- Include batch evaluation and an optional decision trace.
- Use a real xAI key from local `.env`; commit `.env_example`, never credentials.

Non-goals: production banking, external business APIs, human review queues, OCR for image-only scans, currency feeds, vendor verification services, purchase-order matching, web UI, concurrent payments, cross-run deduplication, durable workflow resumption, elaborate provider abstraction, and stock procurement accounting.

The inventory behavior follows the assignment and user instructions: paying an invoice consumes inventory. Do not reinterpret it as receiving supplier goods and increasing inventory.

## 2. Settled requirements

| Topic | Required behavior |
|---|---|
| Reasoning provider | xAI Grok; user has API access |
| Orchestration | LangGraph |
| Approval | Automated VP persona; no human approval step |
| Business decisions | Approve or reject; no `needs_review` |
| Hard blockers | Invalid quantities, unknown items, stock overruns |
| Reflection failure | Stop payment and reject with reasoning |
| Extraction correction | Return to ingestion for clear extraction errors, with a retry cap |
| Currency | Fixed mock FX rates; canonical invoice monetary data stored in USD |
| Run isolation | Fresh inventory and fresh payment ledger for every CLI invocation |
| Directory behavior | Ingest all files first, then process statefully by invoice date |
| Stock changes | Decrement after successful payment; later invoices see reduced stock |
| Duplicate protection | Within the invocation only; identify duplicates and changed versions |
| Trace | Detailed decision trace behind a CLI flag |
| Evaluation | Batch evaluation in scope |
| Reset behavior | Fresh schema and seed data every invocation supersedes earlier optional reset flag proposal |

## 3. Defaults that close remaining ambiguity

Implement these defaults unless the user changes them. None requires architecture redesign.

1. FX: `USD = 1.00 USD`, `EUR = 1.10 USD`. These are deliberately fictitious fixed simulation rates, not market quotes. Unsupported currencies cause rejection. Unspecified currency defaults to USD with a recorded assumption; `$` in the provided fixtures means USD.
2. Monetary arithmetic inconsistencies are payment blockers. A one-cent absolute tolerance applies to source-currency totals; larger differences block payment.
3. Missing vendor, invoice number, invoice date, due date, items, or a positive payable total blocks payment once extraction recovery is exhausted or the source clearly omits the value.
4. A mismatch between explicit due date and supported payment terms produces a warning requiring VP acknowledgment. Due date before invoice date blocks payment. Do not reject merely because the fixture is overdue relative to today's date.
5. Equal invoice dates are ordered by source filename, then relative path. Directory enumeration is non-recursive for the MVP.
6. A changed version with the same invoice identity as a successfully paid invoice is rejected; no incremental payment or automatic supersession. A prior rejected version does not prevent evaluating a corrected version.
7. An exact repeat of a successfully paid invoice returns an approved result with `payment.status = already_paid`, referencing the earlier payment. It never calls payment again or decrements stock again.
8. Ingestion gets one initial attempt plus at most two corrective attempts. VP review gets one proposal/critique cycle plus at most two revision/critique cycles.
9. Genuine source problems do not consume pointless extraction retries. Missing data explicitly absent in parsed JSON is not a model formatting error.
10. Batch business rejections do not abort remaining files. Provider/configuration failures are reported as operational errors, never disguised as successful agent decisions.

## 4. End-to-end execution

### 4.1 Invocation coordinator

1. Parse CLI arguments and load environment configuration.
2. Discover supported inputs and establish stable source IDs.
3. Create a fresh run ID and a fresh local SQLite database from committed schema/seed SQL. Never reuse a prior run database for execution.
4. Ingest every supported input through the ingestion graph, including any extraction retries and source-consistency checks.
5. Finalize extracted dates before ordering. Collect terminal ingestion failures separately; these cannot be paid.
6. Sort successfully extracted invoices by `(invoice_date, filename, relative_path)`.
7. Execute the processing graph sequentially for each invoice against the shared run database.
8. Emit one structured result per input plus a summary. Include terminal ingestion failures in output even though they have no processing position.
9. Close the database. It may be retained in the run output directory for inspection, but must never become the next invocation's starting state.

No payment may occur while any input still awaits ingestion. Do not validate all stock snapshots before processing: validation must see stock after earlier payments.

### 4.2 Graph boundaries

Use two small LangGraph graphs plus a Python batch coordinator. A single-file call uses the same coordinator with one input.

Ingestion graph:

```text
START -> read_source -> extract_or_map -> check_extraction
check_extraction -> complete -> END
check_extraction -> repair_extraction -> check_extraction  [bounded]
check_extraction -> reject_extraction -> END
```

Processing graph:

```text
START -> check_paid_identity
check_paid_identity -> duplicate_result -> END
check_paid_identity -> reject_version_conflict -> END
check_paid_identity -> validation_agent -> deterministic_validation
 -> vp_propose -> vp_critique
vp_critique -> vp_revise -> vp_critique  [bounded]
vp_critique -> rejection -> END
vp_critique -> final_payment_gate -> payment -> END
final_payment_gate -> rejection -> END
```

The VP may reject on a completed critique even when there are no mechanical blockers, but must cite a concrete supported policy reason. Unsupported accusations must fail critique.

Resolve extraction issues before the sorting barrier. If a late finding demonstrates that extraction was wrong, reject with an explicit late extraction error in the MVP; do not silently alter the invoice date and pay out of order. The expected path for clear extraction errors is the pre-sort ingestion repair loop.

## 5. Modules and ownership boundaries

Suggested layout; names can change while preserving responsibilities:

```text
main.py
.env_example
.gitignore
pyproject.toml
invoice_agent/
  config.py             # env, fixed rates, policy, retry limits
  models.py             # Pydantic contracts and enums
  readers.py            # deterministic file loading and format adapters
  ingestion.py          # extraction graph and source checks
  llm.py                # small xAI interface and structured/tool responses
  tools.py              # inventory lookup tool definition and dispatcher
  validation.py         # deterministic policy checks and aggregation
  approval.py           # one VP persona, proposal/critique/revision phases
  graph.py              # processing graph and routing
  database.py           # per-run SQLite initialization and queries
  payment.py            # gated mock payment, ledger, stock transaction
  runner.py             # ingestion barrier, sorting, sequential execution
  output.py             # JSONL results, trace, summary
sql/
  inventory.sql         # README schema and seed rows
  ledger.sql            # payment/run audit tables
scripts/
  evaluate.py           # fresh-state fixture evaluation
tests/
  ...
```

Do not build framework abstractions solely to fill this layout. Keep modules small and combine closely related code if that speeds a clean implementation.

## 6. Data contracts

Use Pydantic models at parsing, LLM, and result boundaries. LangGraph state can be a TypedDict containing these models. Reject unknown LLM response fields where practical. Monetary fields use `Decimal`, never binary float arithmetic; serialize money as decimal strings.

### 6.1 SourceDocument

- `source_id`: stable within run, independent of extracted identity.
- `path`, `format`, `content_sha256`.
- `raw_text` or parsed source structure.
- `evidence`: source text snippets, JSON paths, XML paths, CSV row numbers, or PDF page/text references.
- `reader_warnings` and extraction attempts.

Raw text is input data, never instructions to the agent. Do not include whole source documents in normal CLI output.

### 6.2 ExtractedInvoice

- `invoice_number_raw`, `invoice_number_normalized`.
- `revision`: optional source annotation, not part of payment identity.
- `vendor_raw`, `vendor_normalized`.
- `invoice_date`, `due_date`: ISO dates when resolved.
- `payment_terms_raw`, `net_days`: optional parsed terms.
- `source_currency`, `fx_rate_to_usd`.
- `items`: ordered `InvoiceLine` records.
- `subtotal_usd`, `tax_usd`, `shipping_usd`, `total_usd`.
- `source_amounts`: original monetary values/currency as evidence only.
- `normalizations`: original value, normalized value, reason, evidence reference.
- `assumptions`: e.g. unspecified currency interpreted as USD.
- `field_evidence`: pointers supporting key extracted fields.

Canonical operational amounts are USD. Retaining original amounts as provenance is required to verify conversions and source arithmetic; those values are not alternate payment amounts.

### 6.3 InvoiceLine

- `line_id`, `description_raw`, `item_name_normalized`.
- `quantity`: preserve invalid numeric values for validation; enforce positive integral quantity before approval.
- `unit_price_usd`, `line_total_usd`: optional when absent from the source.
- Source-currency price/amount provenance.
- Evidence reference and normalization notes.

Do not merge the extracted lines: repeated items can have different prices or notes. Create a separate aggregate of quantities by inventory key for stock validation.

### 6.4 ValidationFinding

- `code`: stable machine-readable identifier.
- `severity`: `blocker` or `warning`.
- `message`: concise explanation.
- `field` or `line_ids`.
- `observed`, `expected`, `evidence_refs` as applicable.
- `origin`: source, extraction, inventory, arithmetic, identity, or policy.

Representative codes: `INVALID_QUANTITY`, `UNKNOWN_ITEM`, `OUT_OF_STOCK`, `INSUFFICIENT_STOCK`, `MISSING_REQUIRED_FIELD`, `INVALID_DATE`, `DUE_BEFORE_ISSUE`, `TERMS_DATE_MISMATCH`, `TOTAL_MISMATCH`, `INVALID_AMOUNT`, `UNSUPPORTED_CURRENCY`, `VERSION_CONFLICT`, `EXTRACTION_EXHAUSTED`, `REVIEW_EXHAUSTED`.

### 6.5 Approval contracts

Proposal:

- `decision`: `approved` or `rejected`.
- `reason_summary`: concise externally useful explanation.
- `finding_codes`: referenced validation findings.
- `checks`: explicit findings for required VP checklist entries.
- `high_value_review`: required when total USD is strictly greater than 10000.

Critique:

- `verdict`: `accept` or `revise`.
- `issues`: concrete unsupported statements, missed findings, or policy errors.
- `required_changes`: actionable corrections.

Return concise decision rationales and evidence, not private chain-of-thought transcripts.

### 6.6 Result and graph state

State includes source, extracted invoice, findings, actual tool results, proposal, critique, attempt counters, ordered trace events, and payment result. Counters must have explicit semantics rather than relying on the framework recursion limit.

Result:

- `run_id`, `source_id`, `source_path`, `processing_index` when sortable.
- Invoice identity, date, total USD and conversion metadata.
- `status`: `completed` or `error` for execution outcome.
- `decision`: `approved`, `rejected`, or null for an operational failure.
- `reasons` and validation findings.
- `payment`: status `paid`, `already_paid`, `not_paid`, or `failed`; payment ID when available.
- Inventory deltas and attempt counts.
- Optional trace only when requested.

An operational `error` is not a third business-review state. Never introduce `needs_review`.

## 7. Ingestion implementation

### Structured formats

- JSON: parse with decimal-aware numeric handling, map known field names, retain nested vendor and repeated lines.
- XML: parse safely without external entity/network resolution, map the provided header/items/totals layout.
- CSV: inspect headers. The `field,value` layout requires a stateful line-item accumulator because item/quantity/unit_price keys repeat. The tabular layout requires separating item rows from subtotal/tax/total rows.
- Unknown structured layouts may go through Grok using the parsed representation and source evidence. Never silently discard unrecognized totals or line records.

### Text and PDF

- TXT: decode text with clear failure reporting, then call Grok for schema-constrained extraction.
- PDF: use one library, preferably pdfplumber or PyMuPDF, for text extraction. Preserve page boundaries. Empty/image-only PDFs get an explicit unsupported/extraction result; do not claim OCR capability.
- The repository generator creates text PDFs. Verify actual supplied PDFs during implementation rather than relying only on the generator.

### Normalization rules

- `INV 1012` and `INV-1012` may normalize to the same invoice number; preserve the original.
- Case/whitespace normalization may map `Widget A` to `WidgetA` and `Gadget X` to `GadgetX` using known inventory aliases.
- `WidgetA (rush order)` may map to `WidgetA` while retaining the qualifier and original price.
- Do not fuzzy-match `WidgetC` into `WidgetA` or invent inventory items.
- Do not rewrite a vendor's legal name based on spelling intuition.
- OCR-like substitutions such as `2O26` and `3,500.O0` must be local, context-supported, and recorded.
- Relative date `yesterday` has no reliable reference date in the invoice; flag it as unresolved rather than deriving it from execution time.

### Recovery

Check required fields, source coverage, and schema integrity before the ingestion barrier. A model omitting a clearly present invoice date or item triggers repair with the source and specific errors. A source explicitly containing a negative quantity or absent vendor yields findings rather than invented replacements.

Use one initial attempt and two repair attempts maximum. Record attempts and terminal reasons. Keep transport retries separate from semantic correction counters.

## 8. Validation and tool use

Expose a read-only `lookup_inventory(items: list[str])` tool that returns exact inventory keys and integer stock, plus explicit missing-item entries. It must query the current run SQLite database using parameterized SQL.

The validation agent must actually request this tool through the LLM tool-calling interface. A Python node calling SQLite on its own does not fulfill the LLM function-calling demonstration. Tool arguments and results are recorded in the trace. Bound tool rounds (default: two) and validate argument shape.

The deterministic validator checks that lookup results cover every normalized item. The model cannot evade validation by omitting an item from its requested lookup. Missing coverage must be recovered or reported as an operational failure; payment never proceeds with incomplete coverage.

Deterministic checks:

1. Required identity/date/payable fields.
2. Positive integral quantities; reject zero, negative, fractional, or nonnumeric quantities.
3. Aggregate quantities across all lines for each normalized item.
4. Unknown item, zero stock, or aggregate quantity greater than current stock.
5. Positive finite total, nonnegative unit prices/tax/shipping where supplied.
6. Source-currency line math, subtotal, and total reconciliation where the necessary fields exist.
7. Currency conversion under the fixed rate table.
8. Explicit dates and supported terms consistency.

Never invent absent tax/shipping to reconcile a discrepancy. Missing optional line prices do not alone block an invoice when the required payable amount and quantities are supported by the source; record which arithmetic checks were unavailable.

Money arithmetic: validate original amounts first. Convert individual monetary values with Decimal and round to cents using ROUND_HALF_UP. Convert the authoritative source total directly for payment; do not replace it with a sum of separately rounded USD line values. Record any conversion-only rounding residual separately from source inconsistencies.

### INV-1002 worked example

Extract GadgetX quantity 20, invoice date 2026-01-30, due date 2026-01-30, amount USD 15000, and Net 30 terms.

- Quantity 20 and Net 30 are different concepts, not contradictory quantities.
- Lookup returns GadgetX stock 5 on a fresh run: `INSUFFICIENT_STOCK`, blocker.
- Net 30 implies 2026-03-01, inconsistent with stated due date: `TERMS_DATE_MISMATCH`, warning by default.
- VP must reject due to stock and mention the terms finding. Never reinterpret quantity as 30 or repair a faithfully extracted source inconsistency.

## 9. VP agent and reflection

Use one automated VP persona with three phase-specific prompts/functions: propose, critique, revise. These can be separate graph nodes without being separate personas. Grok handles all phases.

Proposal receives canonical invoice fields, source evidence as needed, deterministic findings, and actual inventory results. Critique receives the same evidence plus the proposal and a fixed checklist. Revision receives the critique and prior proposal. A distinct model call is required for critique; asking the initial response to claim it reflected is insufficient.

Checklist:

- Every blocker forces rejection.
- No invented vendor verification, purchase order, inventory, or payment facts.
- Warnings are acknowledged and interpreted using declared policy.
- Amount and currency are correctly understood.
- For > USD 10000, explicitly assess arithmetic, aggregate stock, completeness, and supported suspicious signals.
- Suspicious wording is evidence of urgency, not proof of fraud.
- Decision and rationale agree.

An invoice over the threshold may be approved when all mandatory checks pass. The threshold alone is not a rejection rule.

After two unsuccessful revisions, emit rejection with `REVIEW_EXHAUSTED` and concrete outstanding issues. The final deterministic payment gate always checks the accepted decision, blockers, identity, and current stock. An LLM cannot waive these requirements.

## 10. SQLite, identity, and payment

Commit the README's seed schema and rows, preserving at least:

```sql
CREATE TABLE inventory(item TEXT PRIMARY KEY, stock INTEGER);
INSERT INTO inventory VALUES
  ('WidgetA', 15),
  ('WidgetB', 10),
  ('GadgetX', 5),
  ('FakeItem', 0);
```

Additional ledger tables should record run ID, canonical vendor/invoice identity, semantic fingerprint, payment ID, amount USD, successful status, source ID, and timestamp. Enforce uniqueness for successful payment identity within the run.

Identity = conservatively normalized vendor + normalized invoice number. Revision is metadata, not a new identity. A semantic fingerprint should include normalized identity, dates, canonical line items/quantities/prices, currency provenance, and payable totals. Exclude file format, path, whitespace, incidental missing optional prose, and formatting-only changes. Normalize equivalent omitted zero charges carefully so a TXT/PDF pair does not conflict solely due to presentation.

Do not deduplicate by file hash alone: the fixtures contain the same invoice in multiple formats. Conversely, a different total or changed item set must not be treated as an exact repeat.

Payment sequence:

1. Begin a local SQLite transaction, obtaining an appropriate write lock.
2. Recheck successful payment identity and aggregate stock.
3. Verify accepted approval and zero blockers.
4. Call local `mock_payment(vendor, amount_usd)` exactly once for this payment attempt. The mock returns structured success/failure and performs no external side effect.
5. On success, insert ledger entry and decrement stock within the same transaction; commit.
6. On failure, roll back stock and ledger changes and report the failure. Do not automatically retry payment in the MVP.

The mock should not print a success message before commit; normal output comes from the committed result. Atomicity here applies to local simulated state, not an actual bank transfer. Do not claim production exactly-once banking semantics.

Fresh database per invocation means rerunning a file can pay it again in a new simulation. Document this intentional behavior prominently.

## 11. CLI, output, and configuration

Required examples:

```bash
python main.py --invoice_path=data/invoices/invoice_1001.txt
python main.py --invoice_path=data/invoices
python main.py --invoice_path=data/invoices --trace
python scripts/evaluate.py
```

Recommended optional flags: `--output_dir` for run artifacts and `--trace` for detailed events. Avoid adding unnecessary modes. Do not require `--reset_inventory`: every invocation resets both inventory and ledger by construction.

`.env_example` should contain placeholders for `XAI_API_KEY`, `XAI_MODEL` (initial intended model `grok-3`), and the API base URL if the chosen client needs it. Verify current official xAI client/API/model support during implementation; the README SDK example is illustrative, not a guaranteed working integration. Do not silently switch provider/model when unavailable.

Use a small LLM adapter supporting typed structured output and tool calls. Pin tested dependency versions. Load `.env` locally and ignore `.env`, run databases, generated run outputs, Python caches, and local virtual environments in git. Preserve `.env_example` as tracked content.

Default stdout: JSONL result records and one summary record. Human diagnostics and progress go to stderr so stdout remains machine-readable. Trace includes node transitions, source references, normalizations, lookup requests/results, proposal/critique summaries, retry counts, and payment effects. Never log keys or hidden reasoning.

Summary: discovered inputs, completed results, approved, rejected, duplicate skips, operational errors, number of new payments, total newly paid USD, and final inventory. Do not count duplicate skips as additional paid dollars.

Default exit codes:

- 0: processing completed, including ordinary business rejections.
- 1: any operational error, including exhausted provider failures.
- 2: CLI/configuration failure such as missing key, invalid path, or no supported inputs.

Unrecognized files in a folder are listed as skipped in the summary; an explicitly supplied unsupported file returns a clear CLI error. One malformed supported file yields its own result and does not suppress other files.

## 12. Evaluation and acceptance criteria

Use two distinct evaluation layers without changing runtime folder semantics:

1. Isolated fixture evaluation: run each fixture with its own fresh database to test its intrinsic extraction/validation behavior.
2. Stateful folder integration: ingest all inputs first and run date-ordered payments against one seeded database. Expect outcomes to differ after stock is consumed.

Use deterministic scripted LLM responses for routine tests; these are test doubles, not claims of live model reasoning. Add an opt-in real xAI smoke test for tool calling, extraction, and VP critique. Avoid requiring API calls for the full unit suite.

Minimum meaningful tests:

- Both CSV layouts preserve all items and correctly separate summary rows.
- JSON/XML preserve invalid values for validation rather than repairing facts.
- TXT/PDF extraction contract supports source-backed normalization.
- INV-1002 rejects for stock and flags date/terms mismatch.
- INV-1009 rejects for invalid quantity and missing data.
- INV-1008/1016 reject unknown items.
- Repeated item quantities aggregate correctly; INV-1013 exceeds stock and its JSON total differs by USD 50 from subtotal plus tax.
- INV-1001, original 1004, and 1006 pass stock/arithmetic validation in isolation.
- EUR values convert to USD under the fixed rate; the high-value threshold uses converted total.
- Exactly USD 10000 does not require the >10000 review; one cent above does.
- A valid synthetic high-value invoice can pass enhanced VP scrutiny. Existing high-value fixtures mostly have other blockers, so they are insufficient alone.
- A proposal approving a hard blocker is corrected by critique, or rejected at exhaustion; payment never executes.
- Extraction omission triggers bounded repair; source absence does not lead to fabricated data.
- All ingestion completes before the first payment.
- Date order takes precedence over filenames; equal-date ordering is deterministic.
- Two individually valid invoices can cause a later stock rejection in a shared run.
- Exact paid duplicates do not pay or decrement again; changed paid versions reject.
- Separate invocations both start with fresh stock and an empty ledger.
- Mock payment failure leaves stock and successful ledger entries unchanged.
- Trace flag controls detailed events without removing ordinary rejection reasons.

The fixture evaluator should report expected finding codes and observed results, with extraction failures distinguished from business rejections. Do not hard-code invoice IDs into business logic or fake desired results.

Do not treat the README's clean examples as a promise that they will pass after earlier invoices consume stock. Nor should every fixture be forced to have only one finding: the samples contain overlapping problems.

## 13. Five-hour implementation plan

Suggested allocation, including integration and documentation:

1. 0:00–0:40 — contracts, configuration, SQL setup, readers, CLI skeleton.
2. 0:40–1:40 — xAI integration, ingestion graph, evidence and bounded repair.
3. 1:40–2:25 — lookup tool, deterministic validation, currency and identities.
4. 2:25–3:15 — VP phases, critique routing, final gate and local payment transaction.
5. 3:15–4:15 — coordinator, date-ordered batch behavior, trace, evaluation/tests.
6. 4:15–5:00 — real API smoke test, supplied PDF verification, failure-path checks, README usage and demo artifacts.

Prioritize a working vertical slice early. If scope pressure arises, cut optional output polish and unsupported-layout generalization before cutting tool calls, bounded reflection, batch ordering, payment blockers, or tests of state changes.

No additional agents are required by this specification. If implementation is delegated separately, use the module/data-contract boundaries above and integrate continuously; shared contracts and graph routing need a single coherent owner.

## 14. Definition of done

- Required single-file CLI works using xAI credentials from local `.env`.
- Folder invocation ingests everything before any payment, sorts by extracted invoice date, and processes statefully.
- Every invocation starts a fresh inventory and ledger.
- All required formats work on the supplied fixtures, with explicit failures for unsupported inputs.
- Validation uses actual LLM inventory tool calls and deterministic blocker enforcement.
- VP proposal, critique, and bounded revision are visible with `--trace`.
- No invalid, unresolved, duplicate, or conflicting payment slips through the final gate.
- Successful payments decrement inventory exactly once within the run.
- USD normalization and mock FX assumptions are documented and auditable.
- Automated evaluation exercises isolated fixtures and stateful batches.
- README describes setup, commands, architecture, limitations, and representative outcomes.
- No credentials, local environment files, or run databases are committed.

## 15. Remaining questions

No large architectural question blocks implementation. The default mock EUR rate, warning-versus-blocker treatment of payment-term mismatches, and first-successful-version policy are explicit, configurable policy choices. The user can adjust them without changing the graph or storage model.

The current xAI SDK/model capability and supplied PDF text extraction remain implementation verification tasks, not questions the user must answer before work begins.
