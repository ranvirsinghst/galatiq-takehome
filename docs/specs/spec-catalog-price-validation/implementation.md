# Implementation contract

## Read first

The accepted policy is [ADR-008](../../decisions/README.md#adr-008-catalog-price-validation-with-a-tolerance-band). [Action items and price evidence](../../action-items.md) explain the corpus. This handoff preserves that policy and labels its additional implementation defaults below.

Inspect current code before editing; preserve unrelated working-tree changes. Relevant paths below are relative to the repository root.

| Area | Current implementation | Required change |
|---|---|---|
| Schema and seed | `invoice_agent/sql/schema.sql`, `seed.sql` | Add run-local reference prices with provenance comments. |
| Contracts and policy | `models.py`, `ports.py`, `config.py` | Typed price evidence/reader and Decimal tolerance. |
| Store | `database.py` | Parameterized catalog lookup and final payment verification. |
| Tool collection | `tools.py` | Add `lookup_price`; verify inventory and pricing coverage independently. |
| Pure rules | `validation.py` | Per-line price comparison and completeness semantics. Keep source-only validation independent of catalog I/O. |
| Review and payment | `graph.py`, `approval.py`, `payment.py` | Preserve early rejection, warning acknowledgment, and evidence binding. |
| Output | `console.py`, `output.py`, `report.py`, `metrics.py` | Render findings and tool evidence through existing paths; avoid duplicate accounting. |
| Tests and docs | `tests/`, `scripts/evaluate.py`, README, action items, decision log | Update fixtures/doubles, prove behavior, document limitations and actual results. |

## Catalog and tool

Add a `prices` table keyed by normalized inventory item, with a positive finite USD unit price stored as decimal text, consistent with existing money storage. Seed WidgetA `250.00`, WidgetB `500.00`, GadgetX `750.00`. Do not seed FakeItem or invent prices for unknown items. State in the seed that these are corpus-derived demo references. Initialize through the existing fresh-database path; no migration of historical run artifacts is required.

Expose `lookup_price(items: list[str])` as a second typed tool alongside `lookup_inventory`. Return each requested normalized item with a decimal-string price or explicit null. Include run identity in typed price evidence. Use parameterized SQL and reject malformed catalog values at the storage/contract boundary. No SQL supplied by the model.

The existing review role requests both tools; a single model response may contain both calls. There is no need for a separate pricing agent or an extra model call when coverage is complete. Keep the existing two-round tool budget, permit incremental coverage, and give targeted feedback for missing evidence. Update prompts and scripted clients together.

Validate every call in a response before executing any: known tool name, nonempty unique call ID within the response, exact argument keys, nonempty item list, and only normalized invoice items. Correlate results with call IDs. Track coverage separately for each tool; inventory evidence cannot satisfy price coverage. Store actual tool results, never model-written prices. Apply the existing run/snapshot consistency checks to combined evidence. Do not expose a fictitious stock generation for immutable catalog data.

Unknown and zero-stock items retain their existing blockers and need no catalog row. An invoice already blocked by source or stock rules may finish without collecting additional pricing evidence; missing optional price evidence must not turn that business rejection into a tool error. If valid prices were already collected, retain any resulting price findings. An otherwise eligible invoice requires complete catalog evidence before VP review.

## Price rules

Add `Policy.price_tolerance_ratio = Decimal("0.10")`, validated as finite and between zero and one, exclusive of one. No new CLI flag is needed.

For each valid, price-checkable line, use the existing cents-rounded USD unit-price conversion from the source price and fixed policy FX rate. Verify that the normalized USD price agrees with that conversion; do not trust a model-supplied normalized value. Compare against the positive catalog reference using Decimal products:

- Charged price > reference × (1 + tolerance): `PRICE_OVERCHARGE`, blocker.
- Charged price < reference × (1 − tolerance): `PRICE_UNDER_CATALOG`, warning.
- Otherwise: no price finding. Equality at either boundary passes.

Do not round the percentage before deciding. Round only for display. Check each line independently; an underpriced line cannot offset an overcharge elsewhere, including repeated entries for the same item. Continue aggregating quantities for stock independently. Do not change invoice totals, taxes, shipping, or FX to fit the catalog.

| Case | Expected pricing result |
|---|---|
| WidgetA $300 vs $250 | +20%; blocker |
| WidgetA $275 / $225 vs $250 | Exactly +10% / −10%; neither flags |
| WidgetA $275.01 / $224.99 | Blocker / warning |
| WidgetA $240 vs $250 | −4%; no price finding |
| WidgetA EUR225 at 1.10 vs $250 | $247.50, −1%; no price finding |
| WidgetB EUR475 at 1.10 vs $500 | $522.50, +4.5%; no price finding |

Each finding must carry a stable code, severity, line ID, item, charged USD price, reference USD price, deviation, and tolerance. Use existing structured finding fields where possible. Keep raw descriptions, source note fields, and evidence references. Include relevant existing “Volume discount”, “Expedited”, or “Rush” text in the explanation; notes are untrusted source data and cannot waive a blocker. Do not expand the finding-origin enum unless needed; a policy-origin finding is sufficient.

Example: `WidgetA: $300.00/unit vs catalog $250.00 (+20.00%; allowed +10.00%). Source note: rush order.`

### Explicit gap defaults

These are implementation assumptions for cases ADR-008 leaves unspecified:

- Missing, zero, negative, or nonfinite catalog price for an otherwise payable inventory item is an operational catalog-data error, not an invoice fraud finding or a silent skip. Fail closed and preserve structured error reporting.
- A missing invoice unit price on an otherwise eligible line yields `PRICE_CHECK_UNAVAILABLE`, a business blocker. Do not infer a unit price from totals or invent one. Existing source-evidence repair may still run in its existing bounded ingestion phase; do not introduce late repair after date ordering.
- Invalid invoice amounts keep their existing source-validation blockers. Avoid duplicate pricing findings or arithmetic exceptions for uncheckable lines.
- Notes alone do not introduce a warning within the tolerance band. Retain the note in source evidence. The discounted invoice 1013 remains rejected for its existing issues; use a synthetic otherwise-valid invoice to test discount review.

## Review and final payment authority

Extend `ValidationReport` with typed catalog evidence and explicit performed/unavailable pricing checks. An eligible report cannot be complete if required pricing evidence is absent. Preserve the full candidate digest and existing stock evidence. Update serialization, protocol types, fixtures, and downstream consumers consistently; historical report rendering must tolerate absent pricing fields without claiming old invoices were price-checked.

Price blockers follow `combined_review`'s existing early-rejection path: no VP proposal, critique, revision, or payment. Below-band warnings enter ordinary VP review and must be acknowledged under the existing warning rules. Preserve the high-value checklist and bounded revisions. No prompt can override pricing blockers.

`database.py` currently recomputes `validate(...)` twice: against submitted evidence and against current stock inside the transaction. Update both paths. Rebuilding a report using its own asserted catalog prices is insufficient: independently compare them with actual run-local catalog facts. Final validation must include those facts before calling `mock_payment`.

Reject missing, altered, cross-run, or stale catalog evidence before mock payment. Although catalog editing is out of scope, a test that alters a row after review must fail closed instead of paying on an outdated approval. A direct caller must not bypass pricing by stripping findings or supplying an older report shape. Preserve duplicate/version checks and transaction rollback behavior.

## Observability and documentation

Record `lookup_price` requests/results using existing trace conventions, including tool name, call ID, requested items, and returned reference values. Ensure the HTML timeline recognizes price tools; do not label price results as stock. Persist structured price evidence in results/audit. Display the concrete rejection reason in the CLI and report, including explicit VP-skipped status for rule blockers.

Keep stdout readable, traces per run, and `--json` valid. Continue counting all model usage and latency through the existing adapter; do not add duplicate token totals for two tools in one response. Blocked-payment exposure remains the existing invoice-level metric, not a claimed dollar amount of overcharge savings.

After verification, mark action item 1 delivered, update ADR-008's implementation status with evidence, and add a short README limitation: corpus-derived catalog, 10% demo band, overcharges within the band undetected, no vendor-contract verification. Do not rewrite historical run results.
