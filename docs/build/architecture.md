# Implemented architecture

The completed implementation follows [the module specification](../specs/spec-invoice-agent/SPEC.md). This record tracks concrete integration choices; verification status belongs in [status.md](status.md).

## Control flow

A Python invocation coordinator creates a fresh SQLite run database, then invokes an ingestion LangGraph for every discovered document. Ingestion maps known structured formats deterministically and interprets text/PDF through Grok. All candidates terminate ingestion before the coordinator sorts by invoice date and stable filename/path ties.

A processing graph then handles one invoice at a time: paid-identity check, actual inventory tool request, deterministic validation, VP proposal/critique/revision, final eligibility gate, and atomic mock payment. The VP phases share one persona. All cycles have explicit counters independent of graph recursion limits.

## Trust boundaries

Source documents and model output are untrusted data. Candidate models preserve source defects; deterministic validation generates payment blockers. LLM tool requests can read inventory but cannot mutate stock or pay. Only the local transaction service can insert a successful ledger row and decrement aggregate stock.

Semantic payment fingerprints identify equivalent document formats. A separate full candidate digest binds review to the exact facts evaluated. The ledger and inventory live in the same run-local SQLite transaction domain; no external bank side effect is claimed.

## Verification strategy

Model calls are replaced by scripted transport/port responses in offline tests, while graph execution, readers, SQLite, and CLI serialization remain real. Real xAI validation is a separate gate and may not be reported as passed by substituting mocks. Invoice rejection is a normal business outcome; provider/storage failure is an operational error.

## Operational audit

CLI outcomes and full evidence snapshots are flushed per completed invoice to results.jsonl and audit.jsonl. The run directory is reported before processing. SQLite remains authoritative for committed mock payments if interruption occurs between a database commit and artifact writing. Summary-only storage failure preserves outcomes and returns a nonzero exit.
