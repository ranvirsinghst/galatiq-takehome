---
id: SPEC-catalog-price-validation
companions:
  - implementation.md
  - verification.md
sources: []
---

# Catalog price validation

This file and both companions are the dev handoff. Implement the feature, run the checks, review the payment boundary, and record actual results. This spec does not claim implementation is complete.

## Why

An invoice can have correct arithmetic and still overcharge. Invoice 1010 charges $300 for WidgetA against the demo reference of $250, yet currently passes in isolation. Add the catalog check accepted in ADR-008 so this invoice rejects with a concrete reason while legitimate FX differences and discounts still pass.

## Capabilities

- **CAP-1**
  - **intent:** Obtain reference prices for invoice items from the run's catalog.
  - **success:** WidgetA $250, WidgetB $500, and GadgetX $750 are available through real typed tool calls; missing or fabricated evidence cannot count as a completed check.
- **CAP-2**
  - **intent:** Identify overcharges and unusual discounts consistently.
  - **success:** Each line above +10% produces a payment blocker; below −10% produces a warning; exactly ±10% passes. Invoice 1010 rejects and invoice 1014 remains approved in isolation.
- **CAP-3**
  - **intent:** Prevent payment when pricing evidence is invalid or rules fail.
  - **success:** Final payment checks independently verify catalog facts and eligibility. Blockers skip VP review; declined or failed payments leave inventory and ledger unchanged.
- **CAP-4**
  - **intent:** Explain and verify catalog decisions across the CLI and saved artifacts.
  - **success:** Users can see the item, charged price, reference, deviation, and reason; regression tests and real CLI runs demonstrate the behavior specified in verification.md.

## Constraints

- Follow the accepted pricing policy in ADR-008; implementation details and explicit gap defaults are in implementation.md.
- Use Decimal USD and the existing fixed FX policy. Catalog values come from the supplied corpus, not vendor contracts.
- Keep the combined review node, bounded tool recovery, deterministic payment authority, and atomic stock/ledger transaction.
- Preserve fresh state per invocation and stateful chronological folder processing. Add no agent or separate pricing approval loop.
- Preserve existing public output behavior, historical report readability, source evidence, and credential handling.

## Non-goals

- Vendor-specific contracts, live FX, discount tiers, purchase-order matching, catalog editing, raw SQL tools, real payments, or proof of fraud.
- Separate review triggered solely by a note such as “Rush” when its price is within tolerance.

## Success signal

The real CLI rejects isolated invoice 1010 with PRICE_OVERCHARGE and no VP or mock-payment call, while isolated invoices 1001 and 1014 still pay. Offline evaluation changes only invoice 1010's isolated expected decision, and stateful scenarios retain verified stock and duplicate semantics.

## Assumptions

- New defaults for missing prices, threshold precision, and note handling are identified in implementation.md. They close gaps in ADR-008 without requiring additional infrastructure.
- The 10% tolerance is a demo policy, not an empirically validated fraud threshold.
