---

id: SPEC-invoice-agent
companions:
  - ../../galatiq-invoice-agent-spec.md
  - delivery-plan.md
  - shared-contracts.md
  - source-ingestion.md
  - llm-runtime.md
  - inventory-validation.md
  - vp-review.md
  - storage-payment.md
  - batch-cli-observability.md
  - testing-and-evaluation.md
  - concerns-and-decisions.md
sources: []
---

> Current policy update (2026-09-08): the processing graph has one combined review node. Inventory tools and deterministic checks run first; hard blockers (including on high-value invoices) reject immediately with no VP calls. Eligible invoices retain the VP proposal/critique/revision rules below. See the [architecture decision log](../../decisions/README.md) for rationale and superseded behavior.

# Invoice agent module specification

## Why

Deliver the working Galatiq invoice prototype within the five-hour implementation budget while allowing subagents to build separate modules without incompatible schemas, duplicate business logic, or untested payment behavior.

## Capabilities

- **CAP-1**
  - **intent:** Extract invoice facts from the supplied document formats with traceable corrections.
  - **success:** All supplied formats produce evidence-backed candidates or explicit failures; extraction repairs are bounded and complete before batch sorting.
- **CAP-2**
  - **intent:** Identify invoice defects against current inventory and declared policy.
  - **success:** Actual LLM inventory tool requests supply complete lookup evidence; deterministic tests reject invalid quantities, unknown items, overruns, and inconsistent totals.
- **CAP-3**
  - **intent:** Simulate accountable VP approval with self-correction.
  - **success:** Proposal and critique occur in separate calls; revision limits and a deterministic payment gate prevent unresolved or blocked invoices from being paid.
- **CAP-4**
  - **intent:** Pay eligible invoices once within a run while consuming available stock.
  - **success:** Real SQLite tests prove stock/ledger atomicity, duplicate protection, version conflicts, and fresh state on the next invocation.
- **CAP-5**
  - **intent:** Process a file or folder with understandable results and optional detailed traces.
  - **success:** All ingestion precedes payment; payments follow invoice date; JSONL results, exit status, and final stock agree.
- **CAP-6**
  - **intent:** Demonstrate reliable behavior independently of model variability.
  - **success:** Unit, contract, and integration tests run without credentials; separately identified live xAI checks verify extraction, actual tool calls, and reflection.

## Constraints

- Preserve the user decisions in the adopted overall specification, especially fresh inventory AND ledger on every invocation and stateful processing within a folder.
- Use Python, LangGraph, and xAI Grok; business integrations remain local mocks.
- Freeze shared contracts first; each implementation agent owns its designated files and tests.
- Monetary calculations use Decimal; operational invoice values and payments use USD.
- Input facts do not become agent instructions; invalid source facts are preserved for rejection.
- No module may bypass payment policy or hide infrastructure errors as business decisions.

## Non-goals

Production banking, persistent cross-run state, OCR, human review, a web UI, arbitrary format support, model-provider expansion, and production-grade workflow resumption.

## Success signal

A fresh installation runs single-file and folder demos plus an offline test suite. Integration evidence proves the ordering barrier, stock consumption, duplicate handling, rollback, real graph reflection, and absence of payment for blocked invoices. Live verification is reported honestly as passed, failed, or not run.

## Assumptions

The policy defaults and implementation limitations in [concerns-and-decisions.md](concerns-and-decisions.md) remain adjustable without user approval being required to begin implementation.

## Reading and precedence

Read the adopted overall specification for business detail, then shared contracts, the assigned module, and the testing contract. This package refines module seams; explicit user instructions take precedence. Raise an actual conflict with the integration owner instead of silently choosing a different policy.

Start assignment and scheduling from [delivery-plan.md](delivery-plan.md). This package is the build entry point; the original handoff is historical context.
