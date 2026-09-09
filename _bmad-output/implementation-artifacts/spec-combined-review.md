---
title: Combined invoice review with deterministic rejection
created: 2026-09-08
type: feature
status: done
route: oneshot
baseline_commit: d1f66156674667fb0ff0a21d7b870fb6f514f391
---

<frozen-after-approval reason="User approved combined review and requested solid documented decision rationale">

## Intent

Use one processing review node for inventory tool validation and VP review. Deterministic blockers reject immediately with reasons and no VP model calls, including high-value blocked invoices. Otherwise preserve VP proposal, independent critique call, bounded revision and final deterministic payment safeguards. Consolidate high-level architecture decisions with rationale, alternatives, consequences and evidence.

</frozen-after-approval>

## Implementation Notes

Pure validation remains independently testable; combining orchestration does not combine or weaken trust boundaries. Inventory still uses actual function calling; only redundant VP calls for mandatory rejection are removed. Audit retains deterministic report, null VP review, explicit zero VP calls and a decision event. Original high-value checklist still applies to all eligible VP-reviewed invoices, regardless of the model's eventual approval/rejection. Preserve validation/approval latency categories for comparison without double-counting a combined wrapper. Core graph changes and decision log run independently; root verifies CLI/evaluation/live paths and updates active specs.


## Review Triage Log

- Medium, fixed: inserted policy note was inside SPEC.md YAML front matter. Moved it below metadata delimiters and parsed the front matter to verify the expected specification ID.
- Independent review found no additional payment-safety or functionality regression; combined review, batch, and CLI subprocess checks passed. No findings deferred.

## Verification

311 unit/integration tests pass; both actual xAI live tests pass in 33.39 seconds. Offline evaluator 23/23 passes. Real two-file CLI: 1001 paid, 1002 rejected with rule reasons and zero VP calls; 6 model calls versus earlier 8. Observed 30.4 seconds and USD 0.021230, qualified as single-run observations rather than guaranteed improvements. Full supplied-folder live verification passes: 20 completed, 2 payments USD 6,890, 18 rejected, 0 operational errors. 17 deterministic rule rejections have zero VP calls, remaining rejection is paid-version conflict. 40 responses/213.7 seconds observed versus historical 81/405 seconds; qualified comparison, not benchmark. Ledger and stock verified directly.
