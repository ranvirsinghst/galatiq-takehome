---
title: Concise user-facing console messages
type: refactor
created: 2026-09-09
status: done
route: oneshot
baseline_commit: 19b4646ae70f37faf4f0844bf2d84fca29aaff6a
---

<frozen-after-approval reason="User requested brief, clean runtime messages">

## Intent

Make terminal output brief and understandable to non-technical users. Show useful progress and outcomes, with relevant rationale, without overwhelming detail. Preserve invoice processing, saved evidence, and machine-readable output.

</frozen-after-approval>

## Implementation Notes

Small presentation-only change with no intent gaps or irreversible actions. Preserve existing workspace report changes. ConsoleReporter owns progress, outcomes, and summary; main.py owns startup and failure notices. Keep stdout/stderr separation for scripts. Suppress routine review internals and repeated extraction completion, explain oldest-first ordering, bound displayed reasons and warnings, and keep technical metrics in reports/artifacts. Update existing console/CLI assertions and README. Verify offline tests, lint, and types; no paid provider calls needed.


Implemented concise progress, simulated-payment labels, bounded and deduplicated explanations, brief completion summary, plain failure notices, and updated documentation. Preserved JSON stdout and all saved artifacts. Existing report edits remain intact. Console changes staged separately from overlapping pre-existing report work for a focused commit.

Verification: 333 offline tests passed, 2 live tests deselected; Ruff and mypy passed. Regression coverage includes immediate flushing, JSON/ledger compatibility, error and skip context, shortened explanation notices, warning limits, and concise summaries.

## Review Triage Log

- Low, patched: shortened error messages lacked a details pointer; omission tracking now covers errors.
- Medium, patched: common provider/schema/tool errors exposed internal vocabulary; present brief descriptions and point to original saved detail.
- Medium, patched: distinct error reasons were suppressed; error outcomes now retain bounded reasons, including skipped processing after infrastructure failure.
- Low, patched: long reasons could shorten to identical lines; deduplicate their displayed forms.
- Low, patched: unsupported-input count hid filenames; point to the report's skipped-input list.
- Medium, addressed: added direct regression coverage for concise defaults rather than relying only on legacy detailed-rendering tests.
- False: CLI --trace does not control terminal detail; main constructs ConsoleReporter without trace=args.trace. README correctly describes artifact-only CLI behavior.

Only the oneshot blind review layer was used; no findings deferred.

Follow-up: show the oldest-first rationale only for runs containing multiple invoices; single-invoice progress and batch CLI checks pass.
