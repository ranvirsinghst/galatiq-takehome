# Important concerns and recorded defaults

No unresolved high-level architectural question blocks implementation. The following concerns require explicit implementation behavior and evidence, not another broad design round.

| Concern | Decision/default | Owner and proof |
|---|---|---|
| Parallel agents invent incompatible models | Freeze candidates, outcomes, ports and event schema first; one shared owner | A contract tests; G integration |
| Invalid values disappear at parsing | Candidate preserves raw/invalid tokens; payment request is strict | A/B/D negative, boolean, missing-field tests |
| Duplicate detection differs by format | Semantic fingerprint with proven optional amount equivalence, sorted line multiset | E; real 1011 TXT/PDF integration |
| Equivalence hash omits review-relevant facts | Separate full candidate digest binds validation/review/payment; equality hash is only for duplicates | A/E/F changed-terms binding test |
| Revised invoices may be paid twice | First successful identity wins within run; changed paid version rejects | E/G original/R1 scenario |
| All ingestion first conflicts with late repairs | Correct before sorting; freeze candidates; reject late extraction error | B/G barrier and late-error tests |
| LLM extraction is not provably complete | Source evidence checks and known omission tests; document arbitrary free-text limitation | B actual fixtures/live extraction |
| Critic may approve a bad proposal | Deterministic policy/checklist gate plus transaction guard | F/G/E hostile-model tests |
| Model omits item from tool request | Independent full-item coverage check, bounded recovery | D tool protocol tests |
| Retry loops multiply | Separate counters, explicit request bounds and timeout; disable nested SDK retries | A/C/F call-count tests |
| 'Rejected' hides outage | Domain findings versus operational errors, defined result/exit taxonomy | A/G fault tests |
| Stock and ledger diverge on failure | One SQLite transaction, guarded updates, emit success only after commit | E failure-injection tests |
| Sequential folder changes fixture outcomes | Isolated evaluation differs from stateful runtime; fresh state per invocation only | H distinct suites |
| High-value fixtures all have blockers | Add valid synthetic >10000 case | F/H enhanced-review acceptance |
| Currency roundoff creates false mismatch | Validate source arithmetic, convert authoritative total once, preserve provenance | B/D boundary tests |
| Trace leaks secrets or breaks JSON output | Central event schema, sanitization, stdout/stderr separation | A/C/G redaction/serialization tests |
| xAI README example is illustrative | Verify official supported client/model/features; no silent fallback | C live gate |
| Five-hour scope versus testing breadth | Parallel ownership, short contract phase, early vertical slice; prioritize invariant tests | G delivery checkpoints |

## Policy defaults carried forward

- EUR mock exchange rate is 1.10 USD; USD rate is 1.00. These are simulation constants.
- Net-terms/date mismatch is a warning; due-before-issue and monetary inconsistency are blockers.
- Missing currency assumes USD with an explicit record.
- Source arithmetic tolerance is one cent; date-only sorting has stable filename/path ties.
- Missing or unresolved invoice date rejects; relative due date without source reference rejects.
- No automatic revision supersession, incremental payment, or cross-invocation duplicate protection.

These defaults were proposed in the overall spec rather than all individually dictated by the user. They are centralized and can change without redesigning the system. Do not interpret them as permission to change settled user decisions about invocation state or ordering.

## Known practical limits

A folder run that successfully pays original 1004 will reject its later R1, even though all sources were ingested first. Ingestion-first is not a mandate to choose the latest revision. If the desired business behavior changes to 'latest revision wins before any payment,' that is a new policy with new ordering/identity tests.

Every invocation can simulate paying the same invoice again because ledger reset is deliberate. A retained database is an audit artifact, never active cross-run state.

Source-grounding checks detect concrete errors but cannot guarantee all free-text interpretations are correct. The prototype does not claim verified vendor identity, fraud detection certainty, or actual bank-transfer guarantees.

## Specification maintenance

This package refines the adopted overall specification. The `.memlog.md` records decisions and validation verdicts; use installed BMAD helpers for subsequent updates. Module implementers report contract problems to the integration owner rather than independently editing shared specs or schemas. Keep source data and expected test oracles independently reviewed.
