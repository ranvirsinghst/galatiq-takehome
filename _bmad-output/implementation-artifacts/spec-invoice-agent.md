---
title: Complete local multi-agent invoice processor
type: feature
created: 2026-09-08
status: done
route: dispatch
baseline_commit: 2f150152b962ccd24b35e515e56b04f673d410bc
review_loop_iteration: 0
context:
  - docs/specs/spec-invoice-agent/SPEC.md
  - docs/specs/spec-invoice-agent/shared-contracts.md
  - docs/specs/spec-invoice-agent/testing-and-evaluation.md
---

<frozen-after-approval reason="User explicitly authorized completion of the existing full plan with parallel agents">

## Intent

**Problem:** The repository contains the Galatiq assignment and approved module specs but no working invoice processor.

**Approach:** Implement the complete specified local Python/LangGraph system, verify real ingestion-to-mock-payment behavior, review independently, and deliver a clean commit with honest evidence.

## Boundaries & Constraints

**Always:** Fresh inventory and ledger per invocation. Ingest all folder inputs before sorting by invoice date and processing statefully. Preserve invalid source facts. Enforce deterministic payment blockers, actual LLM inventory tool use, one VP persona with separate critique calls, bounded retries, USD conversion, and atomic local payment/stock effects. Provide JSONL results, optional trace, batch evaluation, and rigorous tests.

**Never:** Real payment, external business APIs, cross-run deduplication, human review state, OCR claims, fabricated live verification, secret commits, or unrequested deployment.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Clean invoice | Seed stock sufficient | Approved after critique; one mock payment and exact stock delta | No partial commit |
| Invalid invoice | Negative/unknown/overstock/mismatched totals | Reject with findings | Continue batch |
| Folder | Dates disagree with filenames | Ingest barrier, date-order payments, shared depletion | Invalid dates reject |
| Duplicates/revisions | Same paid identity | Equivalent skips; changed version rejects | No second stock delta |
| Agent/provider failure | Schema, transient, endless critique | Bounded recovery; explicit terminal result | Never default approve |
| New invocation | Prior run output exists | Fresh DB and ledger | Do not delete/reuse prior run |

</frozen-after-approval>

## Code Map

- `docs/specs/spec-invoice-agent/` — source module boundaries and acceptance contracts.
- `data/invoices/` — provided fixtures, immutable test inputs.
- `_bmad/` — planning helpers, excluded from app lint/coverage.
- `invoice_agent/`, `main.py`, `sql/`, `tests/`, `scripts/evaluate.py` — new implementation surface.
- `docs/build/status.md` — live ownership and evidence tracking.

## Tasks & Acceptance

**Execution:**
- [x] `invoice_agent/models.py`, `ports.py`, `config.py`, `money.py` — freeze shared domain types and dependencies.
- [x] `invoice_agent/readers.py`, `normalization.py`, `ingestion.py` — supplied formats, provenance, bounded ingestion graph.
- [x] `invoice_agent/llm.py`, `approval.py` — xAI adapter and VP reflection graph.
- [x] `invoice_agent/tools.py`, `validation.py` — actual lookup calls and pure checks.
- [x] `invoice_agent/database.py`, `identity.py`, `payment.py`, `sql/` — fresh transactional run state.
- [x] `invoice_agent/runner.py`, `graph.py`, `output.py`, `main.py` — sequential batch coordinator and observable CLI.
- [x] `tests/`, `scripts/evaluate.py` — isolated/unit, real composition, failure injection, opt-in live checks.
- [x] `README.md`, `docs/build/` — usage, actual validation evidence, independent review and final delivery.

**Acceptance Criteria:**
- Given mixed supported inputs, when a folder runs, then all ingestion terminates before processing and successful payments consume stock in invoice-date order.
- Given blocked or unresolved approval, when model output attempts approval, then deterministic guards prevent payment.
- Given equivalent and changed versions, when a prior successful identity exists, then only one new payment occurs.
- Given transport or database faults, when retries exhaust or commit fails, then errors are typed and no partial successful state is reported.
- Given a fresh environment, when offline tests and evaluator run, then they pass without credentials; live verification is separately evidenced with actual xAI.

## Implementation Notes

- Implementation and offline gates complete. Credentials configured; live single-file and full-folder CLI paths plus both live acceptance tests pass. Full live evaluation passes all 23 cases.
- Final verification: 237-test complete offline run plus passing full CLI/HTTP subprocess regression, 23/23 evaluator, 94% branch-inclusive coverage, lint/types and wheel build pass.

- User authorization covers the entire plan, parallel dispatch, existing session-generated dirty docs, review and appropriate commit. Existing unrelated .DS_Store will be ignored/preserved. No new approval checkpoint needed.
- Provider documentation reports grok-3 redirects to grok-4.3; explicit configurable grok-4.3 default is the compatibility choice, announced to user.
- Missing key is a live-verification dependency, not a reason to stop independent implementation.

## Spec Change Log

## Review Triage Log

| Layer / finding | Verdict | Evidence and disposition |
|---|---|---|
| Blind: invalid duplicate source facts | high | Reproduced malformed shipping bypass; source-only validation now blocks duplicate shortcut without using depleted stock. Fixed with regression. |
| Blind: quantity substring evidence | high | Quantity 2 matched source 12; exact labeled/table quantity checks now request correction. Fixed with regression. |
| Blind: omitted monetary evidence | high | Source price/amount could disappear, disabling arithmetic; recognized monetary facts must be preserved. Fixed with regression. |
| Blind: ambiguous comma amounts | high | 1,23 became 123; unsupported grouping/locale now rejected as invalid source amount. Fixed with regression. |
| Blind: CSV tax alias overwrite | medium | Alias applied after conflict detection; canonicalization now precedes conflict check. Fixed with regression. |
| Blind: repeated XML containers | medium | First header/totals/terms silently won; cardinality checks now reject ambiguity. Fixed with regression. |
| Blind: partial JSON recognition | medium | Familiar vendor key suppressed nested-layout fallback; recognition narrowed with regression. |
| Blind: tax-rate consistency | high | Explicit rate inconsistent with tax amount passed; known subtotal/rate math now checked, unavailable bases acknowledged. Fixed with regression. |
| Blind: missing durable audit | high | Exact source/decision facts were lost on exit; audit.jsonl now persists candidate/hash/report/review per outcome. Fixed with regression. |
| Blind: interrupted partial artifacts | high | Successful ledger entries preceded deferred result writing; outcomes now flushed incrementally and run path printed upfront. Fixed with interrupt regression. |
| Edge: accept critique with unresolved issues | high | Contradictory accepted critique authorized approval; issues always trigger revision and payment request rejects contradiction. Fixed with regression. |
| Edge: non-object provider message | medium | AttributeError bypassed schema recovery; envelope guards now emit typed schema error. Fixed with regression. |
| Edge: oversized finite money | medium | Decimal quantization exception became internal error; bounded supported monetary parsing preserves invalid token/finding. Fixed with regression. |
| Verification: summary-only failure CLI exit | medium | Removing summary.error exit condition was untested; actual CLI regression now verifies retained paid result and exit 1. Test added. |

Prior review also resolved final snapshot error preservation, discovery permission errors, real-adapter tool recovery, and duplicate commit events. See docs/build/review-runtime.md and review-integration.md. All fixes preserve original intent and require no new user decision. Live provider acceptance is complete with configured credentials.

## Verification

- `uv run pytest tests/unit tests/integration -q` — offline suite passes.
- `uv run pytest --cov=invoice_agent --cov-branch` — critical invariant coverage examined.
- `uv run ruff check .` and `uv run ruff format --check .` — authored code clean.
- `uv run mypy invoice_agent` — typed module boundaries checked.
- `uv run python scripts/evaluate.py` — independent expected findings match.
- Real CLI single and folder invocations using local xAI key — actual tool/review/payment path verified; report credential blocker honestly if absent.


## Live follow-up review

- Live extraction preserved raw `0%`, revealing that normalization treated a valid percentage as an invalid amount. Added tax-rate-specific Decimal percentage parsing with raw token/evidence preservation and 20 parameterized regression cases. Independent review found no production regression; corrected its tax arithmetic assertion to check the actual `TOTAL_MISMATCH` code.
- High-value rejection checks were required by the deterministic policy but described as approval-only in the prompt. Aligned the VP prompt with the policy and added a regression demonstrating revision despite an accepting critique when required checks are absent.
- Final offline verification: 259 tests; Ruff lint/format and mypy pass. Both real xAI live tests pass. The real full-folder CLI completed 20 invoices with 2 mock payments totaling USD 6,890.00, 18 rejections, and zero operational errors; SQLite and trace invariants checked directly.

- Full real xAI evaluator: **23/23 cases passed**. All acceptance work complete; local follow-up commit records live fixes and evidence.
