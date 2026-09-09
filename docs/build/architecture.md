# Implemented architecture

The architecture follows [the module specification](../specs/spec-invoice-agent/SPEC.md) and the [accepted decision log](../decisions/README.md). The decision log records alternatives, reasons, tradeoffs, and explicit supersession; this document describes structure. Verification status belongs in [status.md](status.md). An accepted change is not a claim that its implementation has passed verification.

## Control flow

A Python invocation coordinator creates a fresh SQLite run database, then invokes an ingestion LangGraph for every discovered document. Ingestion maps known structured formats deterministically and interprets text/PDF through Grok. All candidates terminate ingestion before the coordinator sorts by invoice date and stable filename/path ties.

A processing graph handles one invoice at a time through identity, review, and payment nodes ([ADR-007](../decisions/README.md#adr-007-one-review-node-with-early-deterministic-rejection)). The review node coordinates actual inventory tool requests and independently testable deterministic validation. Known blockers terminate with findings and warnings, without VP calls, including high-value invoices. Otherwise the VP proposes, critiques, and revises within explicit bounds. The VP phases share one persona. The final eligibility gate and atomic mock payment remain independent safeguards. All cycles have explicit counters independent of graph recursion limits.

This consolidates orchestration while retaining separate validation and approval modules. Any performance benefit comes from skipping redundant VP calls on blocked invoices, not from reducing the number of outer graph nodes. Eligible high-value invoices retain enhanced review; deterministic-blocker rejections no longer require a VP high-value narrative. See the decision log for the policy change and its verification requirements.

## Trust boundaries

Source documents and model output are untrusted data. Candidate models preserve source defects; deterministic validation generates payment blockers. LLM tool requests can read inventory but cannot mutate stock or pay. Only the local transaction service can insert a successful ledger row and decrement aggregate stock.

Semantic payment fingerprints identify equivalent document formats. A separate full candidate digest binds review to the exact facts evaluated. The ledger and inventory live in the same run-local SQLite transaction domain; no external bank side effect is claimed.

## Verification strategy

Model calls are replaced by scripted transport/port responses in offline tests, while graph execution, readers, SQLite, and CLI serialization remain real. Real xAI validation is a separate gate and may not be reported as passed by substituting mocks. Invoice rejection is a normal business outcome; provider/storage failure is an operational error.

## Operational audit

The CLI shows filename/stage progress and readable outcomes with a final compact invoice table. Outcomes and full evidence snapshots are flushed per completed invoice to results.jsonl and audit.jsonl. Sanitized detailed events are persisted as emitted to trace.jsonl; metrics.json records token/spend, stage/model latency, errors, and qualified blocked-payment exposure. [Metric definitions](../metrics.md) distinguish estimated exposure from realized savings and operational errors from business rejections.

The run directory is reported before processing. SQLite remains authoritative for committed mock payments if interruption occurs between a database commit and artifact writing. Summary-only storage failure preserves outcomes and returns a nonzero exit.
