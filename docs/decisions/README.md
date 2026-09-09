# Architecture decisions and tradeoffs

This is the durable decision log: why the system behaves as it does, what we gave up, and what would justify changing it. The [implemented architecture](../build/architecture.md) describes the structure; [delivery status](../build/status.md) records verification; the [module specs](../specs/spec-invoice-agent/SPEC.md) define implementation contracts. Build transcripts and `.memlog.md` remain execution history, not the architecture authority.

Explicit user instructions take precedence. A newer accepted decision here supersedes an older specification **only for the change it explicitly names**. Keep the affected specs and architecture synchronized. Accepted policy is distinct from verified implementation: test results belong in delivery evidence, and acceptance alone does not prove a change works.

For a significant policy or architecture change, add or update a decision with context, alternatives, reasoning, consequences, evidence, and a revisit condition. Mark replaced policy as superseded; do not silently rewrite history. Distinguish user requirements, implementation defaults, observations, and hypotheses. Link tests or measured results instead of claiming benefits from design intent alone.

Decisions 001–006 were **documented retrospectively** from the adopted specs and implemented behavior on 2026-09-08. They do not imply that every implementation default was separately dictated by the user. Decision 007 records the user's newly accepted change; its implementation and live validation are tracked separately.

| ID | Decision | Status |
|---|---|---|
| [001](#adr-001-fresh-invocations-stateful-chronological-folders) | Fresh invocations; stateful chronological folders | Accepted, retrospective |
| [002](#adr-002-deterministic-payment-authority-and-usd-accounting) | Deterministic payment authority and USD accounting | Accepted, retrospective |
| [003](#adr-003-one-vp-persona-with-separate-reflection-calls) | One VP persona with separate reflection calls | Accepted, retrospective; blocker scope superseded by 007 |
| [004](#adr-004-local-mock-payment-and-bounded-product-scope) | Local mock payment and bounded product scope | Accepted, retrospective |
| [005](#adr-005-readable-progress-durable-trace-and-qualified-metrics) | Readable progress, durable trace, qualified metrics | Accepted, retrospective |
| [006](#adr-006-source-grounded-ingestion-and-explicit-model-configuration) | Source-grounded ingestion and explicit model configuration | Accepted, retrospective |
| [007](#adr-007-one-review-node-with-early-deterministic-rejection) | One review node with early deterministic rejection | Accepted by user; verification tracked in delivery status |

## ADR-001: Fresh invocations, stateful chronological folders

**Context and decision.** The user explicitly chose fresh inventory **and ledger** for every invocation, while requiring all folder inputs to finish ingestion before sequential processing by invoice date. Stable filename/path ties are an implementation default. Within a run, the first successful payment reserves the normalized vendor/invoice identity; equivalent duplicates skip payment and changed paid versions reject.

**Alternatives and rationale.** Persistent state would make repeat demos depend on earlier runs and contradict the requested simulation. Independent per-file state inside a folder would fail to model stock competition. Paying in discovery order would make allocation depend on filesystem ordering. Automatically selecting the latest revision would add an unrequested business policy.

**Consequences.** Repeat invocations can simulate paying the same invoice again. Retained SQLite files are audit artifacts, never the next invocation's starting database. The ingestion barrier increases time to first decision. Later invoice versions can reject even when all versions were available before payment.

**Evidence.** [Business requirements](../galatiq-invoice-agent-spec.md), [storage specification](../specs/spec-invoice-agent/storage-payment.md), [batch tests](../../tests/integration/test_batch.py), and [duplicate guard tests](../../tests/integration/test_duplicate_source_guard.py).

**Revisit when** real payment, workflow resumption, cross-run idempotency, or latest-version selection enters scope.

## ADR-002: Deterministic payment authority and USD accounting

**Context and decision.** Model outputs and invoice text are untrusted. Pure validation determines blockers such as invalid quantities, unknown items, aggregate stock overruns, required-field defects, and monetary inconsistency. The LLM cannot waive them. A final transactional gate checks approval evidence and current stock before mock payment. Canonical monetary values use Decimal USD; original values remain provenance. Fixed mock EUR/USD 1.10 is a declared default, not market data.

**Alternatives and rationale.** LLM-only eligibility makes repeatable rules probabilistic. Floating-point money introduces avoidable rounding errors. Live FX adds dependency and time variability without improving this simulation. A single fingerprint for both duplicate equivalence and review binding would either reject equivalent formats or omit review-relevant changes; use a semantic payment fingerprint and a separate full candidate digest.

**Consequences.** Clear defects reject with reasons, not `needs_review`. Net 30 is a payment term, not quantity: invoice 1002 rejects for GadgetX 20 versus stock 5; its inconsistent due date remains a warning. The default warning treatment permits VP acknowledgment of a terms mismatch on otherwise eligible invoices. Mechanical checks cannot establish vendor authenticity or prove fraud.

**Evidence.** [Validation](../../invoice_agent/validation.py), [transaction store](../../invoice_agent/database.py), [validation tests](../../tests/unit/test_validation.py), [money tests](../../tests/unit/test_money.py), and [payment fault tests](../../tests/integration/test_storage_payment.py).

**Revisit when** business policy changes warning severity, supported currencies expand, or actual exchange-rate and payment guarantees are required.

## ADR-003: One VP persona with separate reflection calls

**Context and decision.** The user requested automated VP simulation and questioned whether approval and critique need different agents. One persona uses distinct proposal, critique, and revision calls. Reflection gets an initial cycle plus at most two revisions; deterministic guards still govern acceptance. For reviewed invoices strictly above USD 10,000, require written arithmetic, aggregate-stock, completeness, and suspicious-signal assessments. Warnings and unavailable arithmetic checks must be acknowledged.

**Alternatives and rationale.** Separate fictional personas add conceptual overhead without independent organizational authority. A single uncritiqued response loses the reflection demonstration. Unbounded revisions make runtime and spend unpredictable. Separate calls provide an opportunity to challenge a proposal, but using the same model does not establish independent judgment.

**Consequences.** A complete review costs normally two to six model calls. Checklist presence is mechanically enforceable; correctness of its prose is not. The former policy reviewed deterministic rejections, including high-value rejections. **ADR-007 supersedes that behavior only for the known-blocker path**, which now skips VP calls and the high-value narrative requirement. Eligible invoices retain all review gates.

**Evidence.** [VP specification](../specs/spec-invoice-agent/vp-review.md), [approval implementation](../../invoice_agent/approval.py), [approval rules tests](../../tests/unit/test_approval.py), and [reflection integration tests](../../tests/integration/test_review_graph.py).

**Revisit when** measured critique benefit is negligible, latency is unacceptable, or real organizational approval and independent reviewers become requirements.

## ADR-004: Local mock payment and bounded product scope

**Context and decision.** Deliver the assignment's end-to-end workflow locally with Python, LangGraph, xAI, SQLite, and a mock payment function. Stock and ledger commit in one local transaction. Exclude banking, email, cloud deployment, OCR, a web UI, and production resumption.

**Alternatives and rationale.** Real integrations would consume the stated five-hour target and introduce external credentials, failure semantics, and product decisions that are not needed to demonstrate invoice reasoning and safeguards. A print-only payment mock would not prove stock/ledger consistency.

**Consequences.** Actual transaction and rollback tests are possible without moving money. This does not demonstrate exactly-once bank transfers or production readiness. The target time budget is a constraint, not evidence that development finished within it.

**Evidence.** [Assignment](../assignment.md), [storage/payment tests](../../tests/integration/test_storage_payment.py), and [CLI subprocess test](../../tests/integration/test_cli_subprocess.py).

**Revisit when** a production pilot requires persistent state, access controls, external payment reconciliation, or deployment.

## ADR-005: Readable progress, durable trace, and qualified metrics

**Context and decision.** Silent ingestion and terminal JSON made real runs difficult to follow. Show live filename/stage progress and a compact final table; store sanitized detailed events per run. Collect usage, spend, stage/model latency, and separate operational errors from expected rejections. Display potential loss avoided as **blocked-payment exposure**, deduplicated and excluding paid identities, not realized savings.

**Alternatives and rationale.** Streaming raw traces overwhelms the operator. Treating every rejection as an error misrepresents correct behavior. Summing every rejected copy overstates exposure. Claiming actual prevented losses would require a counterfactual and downstream business evidence we do not have. Provider-reported spend takes precedence over a dated fallback pricing snapshot; missing usage remains explicitly incomplete.

**Consequences.** Terminal output is actionable while artifacts support debugging and optimization. Exposure remains a hypothetical amount and cannot validate the assignment's annual-loss baseline. Trace/storage failures are operational failures; persisted payment state remains authoritative. Cost estimates exclude development and local compute.

**Evidence.** [Metric definitions and pricing provenance](../metrics.md), [metric tests](../../tests/unit/test_metrics.py), [audit persistence tests](../../tests/integration/test_audit_persistence.py), and [recorded live reporting evidence](../build/status.md#run-reporting-follow-up).

**Revisit when** billing assumptions change, retention/access requirements arise, or real outcome data can support realized savings and extraction accuracy metrics.

## ADR-006: Source-grounded ingestion and explicit model configuration

**Context and decision.** Use deterministic readers for recognized structured formats and LLM interpretation for free text/text PDFs. Retain raw facts and evidence; clear extraction omissions enter a bounded repair loop before sorting. Configure the provider/model explicitly; the current Grok default and its migration rationale are documented in the README rather than silently falling back to another model.

**Alternatives and rationale.** Sending every structured file through an LLM adds avoidable variability. Arbitrary free-text parsing solely with rules is brittle. Inventing corrections to invalid source quantities hides defects. Late date repair after payments could invalidate chronological stock allocation, so late extraction defects reject in this MVP.

**Consequences.** Known-format and source-grounding checks improve reproducibility but cannot prove complete semantic extraction for arbitrary documents. No OCR means scanned PDFs are outside the supported path. Provider outages are operational failures, not invoice defects; retries have explicit bounds.

**Evidence.** [Ingestion specification](../specs/spec-invoice-agent/source-ingestion.md), [runtime specification](../specs/spec-invoice-agent/llm-runtime.md), [ingestion tests](../../tests/integration/test_ingestion_graph.py), [live examples](../live-examples.md), and [configuration](../../invoice_agent/config.py).

**Revisit when** unseen-source evaluation justifies new readers/OCR, provider capabilities change, or model quality/cost warrants an explicitly evaluated migration.

## ADR-007: One review node with early deterministic rejection

**Status and context.** Accepted by the user on 2026-09-08. The existing outer graph separated inventory validation from VP approval and spent VP calls explaining invoices already blocked by deterministic rules.

**Decision.** The outer processing graph becomes **identity → review → payment**. The review node coordinates actual LLM inventory tool use and deterministic validation. Keep the pure validator independently testable. If validation finds a blocker, immediately return its reasons and retain warnings; do not invoke VP proposal, critique, or revision. This applies even above USD 10,000. Otherwise retain the existing VP reflection loop and deterministic approval checks. Model/tool failures remain operational errors and cannot produce payment. The final transaction gate independently checks current stock and accepted evidence. Extraction retries and the all-ingestion ordering barrier are unchanged.

**Alternatives and rationale.** Merging node names alone simplifies the diagram but does not eliminate model calls. Removing critique for eligible invoices would also remove the semantic reflection the assignment asks us to demonstrate. Retaining full VP review for clear blockers spends time and tokens on a decision the model cannot change. The selected boundary removes that redundant work while preserving reflective assessment where payment remains possible.

**Consequences and tradeoffs.** Compared with a blocked invoice that previously completed all review cycles, the path avoids the corresponding **two to six VP calls**; this is a call-path expectation, not a measured universal latency reduction. Inventory tool calls remain. We lose contextual VP narrative and the high-value checklist on clear deterministic rejections, while retaining authoritative findings, source evidence, and warnings. Node consolidation does not grant the agent payment authority or move business rules into prompts.

**Verification and evidence.** Require regressions proving zero VP calls for normal and high-value deterministic blockers, retained invoice 1002 stock/terms findings, unchanged eligible proposal/critique/revision behavior, operational failure isolation, and payment-gate enforcement. Compare stage/model metrics on equivalent live inputs before claiming runtime or spend improvements. Verification completed: 311 unit/integration tests and both real xAI acceptance tests pass. The same two-input example (1001 and 1002) previously used 8 calls, 49.6 seconds, and USD 0.02815790; the new run used 6 calls, 30.4 seconds, and approximately USD 0.021230. Both paid 1001 and rejected 1002, with unchanged stock. The new trace proves zero VP calls for 1002. These are illustrative observed runs, not a controlled latency/cost benchmark: output lengths, cache state and network timing vary. The structural elimination of blocked-invoice VP calls is regression-tested. Local new-run evidence: `runs/1c4b20e906894235863eaa765c6b6a88/`. The full supplied folder also retained all payment/rejection/stock outcomes: 40 model responses and 213.7 seconds versus the earlier 81 responses and approximately 405 seconds. All 17 rule-blocked invoices skipped VP; one changed paid version rejected at identity checking. This supports the design rationale but remains an illustrative comparison, not a controlled benchmark. Full evidence and run IDs are in [delivery status](../build/status.md#combined-review-follow-up).

**Revisit when** operators need expert commentary on rejected invoices, deterministic findings prove insufficient for resolution, or measured review quality suggests a different reflection boundary. An optional explanation workflow should not change payment eligibility.
