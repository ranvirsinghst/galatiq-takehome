---
title: Readable live invoice progress
created: 2026-09-08
type: feature
status: done
route: oneshot
baseline_commit: 45b295da565d8eca5908a6649f159906fa82f3ea
---

<frozen-after-approval reason="User requests readable live CLI progress">

## Intent

The CLI is silent during ingestion and subsequently floods the terminal with JSON. Default output should show immediate filename-prefixed extraction and processing stages, readable outcomes with reasons, and a run summary. Retain JSONL artifacts and provide explicit --json for scripting. --trace adds readable live diagnostic details. Flush progress before blocking operations and preserve sanitized diagnostics, error exit codes, chronological processing, and payment behavior.

</frozen-after-approval>

## Implementation Notes

No intent gaps or irreversible work. Add a console presenter subscribed to sanitized EventCollector events. Instrument runner start plus provider request stages; use source IDs mapped to filenames. Human results on stdout, live progress on stderr, --json retains machine stdout. Unit/integration tests verify streaming before model return, error and duplicate outcomes, artifact compatibility, and JSON separation. Update README and verify a real live CLI call.


Implemented ConsoleReporter, sanitized event subscription, ingestion start and pre-request provider events, default human output with explicit --json, readable errors, and README examples. All existing JSON artifact formats remain available. Progress write failure disables the subscriber and records progress_output_unavailable without affecting processing.

Verification: full suite 271 passed before the final additional partial-failure regression; targeted CLI/console suite verifies that regression. Ruff, formatting, mypy, and offline evaluation pass. Real Grok single-file trace and two-file folder runs completed; extraction appeared before the first response, both ingestion stages preceded processing, 1001 paid USD 5,000, and 1002 rejected stock 20 versus 5. Live runs: 4e72630c4a36429eaab4e420f815347b and 3c929d0fc0414874a470c73797909b54.

## Review Triage Log

- Medium, patched: startup/error/interruption text bypassed control-character sanitization; shared clean_terminal now covers these paths and hostile-message regression passes.
- Medium, patched: callback OSError could affect invoice decisions; subscriber disables itself and records a diagnostic, with successful-payment regression.
- Medium, patched: summary-only failure said Run complete and omitted unavailable inventory; failure-aware heading and explicit unavailable stock are tested.
- Medium, patched: unexpected failures lacked partial-artifact guidance; both failure handlers now identify the run directory and absence of summary, tested after committed payment.
- Low, patched: retry number referred to failed attempt; message now names failed and next attempt, tested.
- Medium, patched: fake-model test did not prove pre-HTTP display; MockTransport handler now asserts flushed inventory/proposal/critique/revision progress at request entry.

No findings deferred. One blind review layer performed as required by the oneshot workflow.
