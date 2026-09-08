# E — Run storage, semantic identity, and atomic mock payment

## Boundary

Own `database.py`, `identity.py`, `payment.py`, `sql/`, and module tests. Implement A's store protocols. No model dependency is allowed in storage or payment. Payment eligibility is supplied as a typed internal request and verified at the payment boundary; raw LLM output is never a payment instruction.

Own the exported `payment_identity`, `payment_fingerprint`, and `build_payment_request` helpers. PaymentRequest carries immutable candidate/report/review evidence so the boundary can verify digest, amount, quantities, blocker absence, and accepted complete review. Do not accept a bare boolean as proof of eligibility. Enforce the same mechanically checkable checklist requirements as F through shared validation methods, rather than two divergent rule implementations.

Use a new local SQLite file for every invocation, created in a unique run directory even when `--output_dir` repeats. Load the README schema and seeds from committed SQL. Ledger and inventory may share the file so mutations are atomic. Never delete or reopen a previous run's database as the new run state.

## Schema and invariants

- Inventory includes the exact required item/stock rows: WidgetA 15, WidgetB 10, GadgetX 5, FakeItem 0.
- Successful payment ledger records identity, fingerprint, amount USD as exact decimal text or integer cents, payment/source/run IDs, and committed timestamp.
- Unique successful identity within the run; no duplicate row for an exact repeat.
- No stock below zero. Use guarded updates and verify affected row counts, in addition to the precheck.
- Inventory generation increments after each successful stock-changing commit; unchanged by rejects, duplicates, or rollbacks.
- Read-only lookups include requested missing items explicitly.
- Use parameterized SQL and explicit transaction management; do not rely on hidden connection autocommit behavior.

## Identity and fingerprint

Identity: conservative vendor case/whitespace normalization + invoice number normalization. Preserve meaningful punctuation in vendor names; no fuzzy legal-entity matching. Normalize established `INV 1012`/`INV-1012` patterns without removing arbitrary distinguishing characters.

Fingerprint: versioned deterministic JSON over normalized payment-relevant semantics, then a stable digest. Include identity, invoice/due dates, source currency and applicable rate, canonical payable total, and a sorted multiset of normalized line item/quantity/prices. Preserve multiplicity and distinct prices. Exclude filenames, file hash, layout, revision label alone, extraction evidence, optional prose, and payment-term wording.

This equivalence fingerprint is not A's full frozen-candidate digest. Verify the full digest when binding a candidate to validation/review/payment, so a changed payment-term warning cannot hide behind equivalent payable content. Unit-test that changing terms can preserve duplicate equivalence while invalidating the review binding.

Optional amount normalization:

1. Use explicit source subtotal when valid; otherwise derive only if all needed line amounts or quantity/prices are available.
2. Distinguish unknown charges from zero charges. Omitted tax/shipping may equal explicit zero only if complete subtotal/total reconciliation proves total unallocated charges are zero and supplied charge values are nonnegative.
3. Explicit nonzero charges remain part of the fingerprint; do not guess whether unexplained residual is tax or shipping.
4. If optional fields prevent proving equivalence, treat a later same-identity invoice as a changed/uncertain version and reject after a successful prior payment.
5. Never infer missing data merely to force duplicate equality.

Version the canonicalization rules so tests can pin meaning. Equality is deterministic; the LLM must not decide duplicate identity.

## Duplicate and version outcomes

Before processing-time stock validation, check prior successful identity:

- Same identity + same fingerprint: approved/completed, `already_paid`, reference original payment, no lookup needed and no stock change.
- Same identity + different fingerprint: rejected, `VERSION_CONFLICT`, no payment or stock change.
- No prior successful record: evaluate normally, even when an earlier version was rejected.

Perform the same identity guard inside the transaction. The first successful date-ordered version wins; do not reorder on revision labels or choose a later version automatically. Failed payment creates no successful identity reservation. This is intentionally scoped to a simulation invocation.

## Payment transaction

1. Validate request completeness, accepted review, no blockers, digest consistency, and positive USD amount before invoking the mock.
2. Begin a write transaction and recheck identity and stock using current rows.
3. If stock is now insufficient, return rejection with fresh findings. A changed generation alone does not reject when current stock remains sufficient, but must be observable.
4. Call `mock_payment(vendor, amount_usd)` once. It returns a structured result and has no external side effects or success printing.
5. On success, write ledger and guarded stock decrements, then commit.
6. Emit committed payment event only after commit succeeds.
7. On any failure, roll back all local state; report typed failure. No payment retries.

Keep LLM calls outside the write transaction. If rollback cannot restore a usable connection, escalate a fatal run-storage error so G stops remaining payments.

## Unit tests

Identity tests compare literal independently built canonical examples:

- Equivalent decimal encodings and harmless line reordering match.
- Distinct prices, quantities, dates, source currencies, or payable amounts conflict.
- Repeated identical lines retain multiplicity; do not collapse two lines into one incorrectly.
- INV-1011 PDF/TXT candidates match despite omitted zero totals/terms in the PDF.
- Missing unknown charges are not equated to zero.
- INV-1004 original and R1 conflict after successful original payment.
- Invoice number aliases match only the explicitly supported patterns.

## Real SQLite integration tests

Each test uses a temporary file and real SQL, not an in-memory dict pretending to be a ledger:

- Fresh seeds and empty ledger; two separate stores/runs initialize independently.
- Clean payment commits one ledger row and exact aggregate stock deltas.
- Exact duplicate returns original payment ID, calls mock zero additional times, and leaves stock unchanged.
- Changed paid version rejects; rejected prior version does not block later valid payment.
- Invalid request, insufficient stock, and duplicate do not call mock.
- Mock false/failure/exception rolls back.
- Inject failure after mock success, before ledger insert, after stock update, and at commit: no partial committed rows/deltas and no paid event.
- Unique constraint collision and stale stock snapshot cannot double-pay or overdraw.
- Reopen the completed file for inspection and verify persisted committed state; next invocation still creates a fresh file.

Use injected mock and transaction adapter/fault hooks at narrow boundaries for failure injection. Do not add production 'skip policy' flags for tests. The atomicity claim is local simulation state, not exactly-once real banking.
