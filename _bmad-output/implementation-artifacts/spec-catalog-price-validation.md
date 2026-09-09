---
title: Catalog price validation
type: feature
created: 2026-09-09
status: done
route: dispatch
baseline_commit: 3a6d7f332e586df89a4cec9b16415b5716eda457
review_loop_iteration: 0
context:
  - docs/specs/spec-catalog-price-validation/SPEC.md
  - docs/specs/spec-catalog-price-validation/implementation.md
  - docs/specs/spec-catalog-price-validation/verification.md
---

<frozen-after-approval reason="User supplied the handoff and authorized implementation on the existing branch">

## Intent

Implement the complete user-supplied catalog-price-validation handoff, including both companions listed in context. These documents are authoritative and must be read fully. Correct arithmetic must no longer allow invoice 1010's WidgetA overcharge to pass. Preserve legitimate EUR conversion differences and ordinary discounts.

## Boundaries & Constraints

Always use real typed catalog tool evidence, Decimal USD and the fixed FX policy. Require independent catalog verification at the transaction boundary; preserve atomic mock payment, ledger and stock updates. Preserve combined review, bounded recovery, source evidence, historical report readability, fresh invocations and chronological batch ordering. Preserve unrelated untracked files; the user explicitly approved working on the current branch with them present.

Never add a pricing agent, raw SQL tool, live FX, real payments, vendor contracts, editing UI, or speculative pricing rules. Follow every explicit gap default and verification requirement in the handoff.

## I/O & Edge-Case Matrix

| Scenario | Expected behavior |
|---|---|
| USD300 versus 250 | PRICE_OVERCHARGE blocker, +20%, retain rush note; no VP or payment |
| USD275 / 225 versus 250 | Exact boundaries pass |
| USD275.01 / 224.99 | Blocker / acknowledged warning |
| EUR225 / 475 at 1.10 | USD247.50 / 522.50 inside tolerance |
| Missing invoice price | PRICE_CHECK_UNAVAILABLE business blocker |
| Eligible item with missing/invalid catalog | Structured operational error, no payment |
| Existing source/stock blocker | Reject normally without mandatory extra price evidence |
| Fabricated/absent/cross-run/changed catalog evidence | Final gate refuses before mock, stock and ledger unchanged |
| Historical saved reports | Render without claiming pricing was performed |

</frozen-after-approval>

## Code Map

- `invoice_agent/models.py`: frozen Pydantic contracts, ValidationReport, PaymentRequest and shared warning acknowledgment; add backward-compatible typed catalog evidence but reject absent evidence for new payment.
- `invoice_agent/config.py`, `ports.py`: policy and injectable boundaries; immutable catalog has run identity without stock generation.
- `invoice_agent/database.py`, `sql/schema.sql`, `sql/seed.sql`: fresh SQLite creation, parameterized lookup, validate before and inside BEGIN IMMEDIATE; independently bind to actual catalog facts in both validation paths.
- `invoice_agent/validation.py`: reuse source validation and convert_to_usd; extend validate with catalog evidence, per-line completeness and findings without catalog I/O in validate_source.
- `invoice_agent/tools.py`: retain phase inventory and existing graph budget; preflight all calls before any execution, independently collect two tool coverages, correlate IDs and emit named results.
- `invoice_agent/graph.py`, `approval.py`, `payment.py`: retain early blocker rejection, warning review and final authority; ensure pricing completeness participates.
- `invoice_agent/report.py`, `console.py`, `output.py`, `metrics.py`: reuse structured findings/evidence, explicitly label price results and preserve usage accounting.
- `tests/doubles.py`, `tests/fixture_model.py`, tests and evaluator: update real tool requests and explicit validated fixture evidence; do not fabricate outcomes to hide missing coverage.

## Tasks & Acceptance

**Execution:**
- [x] `invoice_agent/models.py`, `config.py`, `ports.py`, `sql/`, `database.py` — add catalog contracts, references, reader and final authority.
- [x] `invoice_agent/validation.py`, `tools.py`, `graph.py`, `approval.py`, `payment.py` — implement per-line pricing and bounded verified tool collection.
- [x] `invoice_agent/report.py`, `console.py`, `output.py`, `metrics.py` — expose actual evidence and precise reasons through existing artifacts.
- [x] `tests/`, `scripts/evaluate.py` — cover every matrix row and full handoff protocol, payment bypass, rollback, batch and CLI requirements; change only 1010 isolated expectation.
- [x] `README.md`, `docs/action-items.md`, `docs/decisions/README.md`, `docs/build/status.md` — record policy limits and actual required offline/live verification with artifact paths.

**Acceptance Criteria:**
- Given the supplied isolated corpus, when offline evaluation runs, then only 1010 changes from approval to rejection and its total remains 7185.00.
- Given eligible 1001 and 1014, when processed with real tool evidence, then payments and inventory decrements commit atomically.
- Given a price blocker, when a model would approve, then no VP or mock call occurs.
- Given invalid payment evidence, when store.pay is called directly, then no mock or writes occur.
- Given the full verification companion, when implementation completes, then required commands and artifact inspections are recorded honestly, distinguishing live observations from offline checks.

## Implementation Notes

No unresolved intent gaps. No irreversible deployment or production data mutation. Broad cross-layer feature footprint; local live xAI verification is expressly required by the supplied handoff. Implementation choices may be refined while preserving the handoff's policy and acceptance criteria. No remote operations; the final build step records verified feature work in a local commit.

Implementation and review corrections complete. Final offline verification: 426 passing tests, 95% branch-inclusive coverage; Ruff check/format, mypy and all 23 evaluator cases pass. Real isolated CLI 1010 rejects without VP/payment; 1001 and 1014 pay; full folder preserves 2 payments/USD6890 and A2/B3/G5/Fake0. Real live tests pass after honoring configured timeout. Full live evaluator passed all 23 cases with exit 0; command/output paths and browser-inspection limitation are recorded in docs/build/status.md.

## Spec Change Log

## Review Triage Log

| Finding | Verdict and evidence | Route |
|---|---|---|
| Blind 1: catalog failure before inventory completion | medium — tools.py returns catalog_error while missing stock items remain; a price-first failed lookup cannot reach the existing rejection path. | patch |
| Blind 2: incomplete tool transcript on recovery | medium — the assistant exchange is appended before returned-evidence checks; ValueError appends a user message without replying to remaining calls. | patch |
| Blind 3: preflight read assertions | medium — event absence alone does not protect the explicit no-read-before-preflight contract. | patch/test |
| Blind 4: warning refusal/bounds | medium — the pricing-specific test proves acknowledgment success only; required refusal and bounded critique interactions need assertions. | patch/test |
| Blind 5: high-value overcharge | medium — 1010 is below threshold; existing high-value tests cover other blockers. | patch/test |
| Blind 6: CLI pricing coverage | medium — renderer calls do not verify exit classification or persisted pricing through subprocess. | patch/test |
| Blind 7: delivery docs absent from diff | medium — docs/status updates were still in progress on the parent; finish and verify evidence before delivery. | patch/docs |
| Blind 8: catalog fault inside transaction | medium — changed-valid-row test does not exercise the AgentError rollback path for invalid/missing current data. | patch/test |
| Blind 9: multi-item incremental coverage | medium — existing new split tests use only one item; add independent multi-item coverage assertions. | patch/test |
| Edge 1: deferred catalog failure ordering | medium — same verified branch as Blind 1, retained as independent review evidence. | patch, grouped with Blind 1 |
| Edge 2: pending tool replies | medium — same verified incomplete-exchange path as Blind 2. | patch, grouped with Blind 2 |
| Verification 1: custom tolerance | medium — valid non-default policy was not exercised, so hardcoding 10% would evade tests. | patch/test |
| Verification 2: catalog outbound reply contract | medium — final report checks do not detect an empty/miscorrelated price message in the next model request. | patch/test, grouped with Blind 2 |

All listed review findings were addressed with targeted regressions, then verified by the full offline suite. The parent inspected the updated production diff and test assertions; no unresolved review findings remain.

## Verification

Run every command in `docs/specs/spec-catalog-price-validation/verification.md`, inspect new branch coverage, actual tool trace events, HTML artifacts and SQLite ledger/stock. Never expose credentials. If credentials are unavailable, finish offline work and explicitly report live checks blocked.
