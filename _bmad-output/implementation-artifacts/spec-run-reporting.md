---
title: Per-run operational and business reporting
created: 2026-09-08
type: feature
status: done
route: oneshot
---

<frozen-after-approval reason="User explicitly requests trace storage, final table and run metrics">

## Intent

Persist detailed trace events per run, remove trace detail from terminal stdout/stderr, and finish with a compact invoice table and useful metrics. Show token usage, estimated API spend, end-to-end and per-agent latency, processing error rate, and potential loss avoided. Preserve live high-level progress and machine-readable --json results.

</frozen-after-approval>

## Implementation Notes

Cohesive reporting change with no destructive effects. Always flush trace.jsonl per event; --trace retains optional verbose embedded artifact evidence but never sends detail to terminal. Store metrics.json (including incomplete run metrics on interruption when storage is writable), and embed metrics in final JSON summary. Capture usage even on schema-rejected responses and time failed calls. Whitelist numeric usage fields rather than redacting token counts or leaking arbitrary payloads. Count processing errors/discovered separately from business rejection rate and global run failures. Potential savings is labeled blocked payment exposure, not realized savings: maximum positive USD rejected amount per normalized invoice identity, excluding identities paid in this run and duplicate files; unknown amounts excluded and counted. Pricing prefers provider cost ticks (1 USD=10^10 ticks); Grok 4.3 token fallback is versioned to 2026-09-08 official rates, including cache and long-context tiers; unknown usage/cost explicitly incomplete. Tests cover redaction, retries/schema failures, accounting, duplicates, latency, partial trace durability, final table formatting and real CLI integration. Live two-file run verifies displayed metrics against artifacts.


Full agent stage timing is separate from model-call timing. Actual provider initialization distinguishes known zero-call runs from missing double telemetry. Trace persistence failure disables trace writes and marks the run error without hiding post-commit payments. Durable report completion is tracked separately from terminal delivery.

## Review Triage Log

- High, patched: trace write failure could hide post-commit paid outcomes. Trace persistence is now non-disruptive and the run is explicitly marked with a trace-storage error; post-commit failure regression verifies ledger and paid result.
- Medium, patched: stdout failure could overwrite complete durable metrics as partial. Mark durable completion before console output; failure regression checks metrics equal saved summary.
- Medium, patched: deterministic zero-call rejected runs were labeled unknown cost. Actual model client initialization now proves zero calls; test covers known zero versus absent telemetry.
- Low, patched: table missing amount on ingestion rejection. Candidate total is now fallback, matching exposure accounting.
- Medium, patched: agent latency was only model-call time. Instrument full ingestion/validation/approval/payment stages and retain model timing separately.
- Low, patched: Unicode filenames misaligned compact table. Display-width clipping and padding handle CJK and combining characters; regression checks columns.
- Medium, resolved: README trace behavior was stale while review ran. Updated README and docs/metrics.md document always-persisted trace, no console detail, metric definitions and cost provenance.
- Additional root check, patched: candidate-only identity fallback could miss vendor whitespace normalization. Reuse payment_identity for consistency with committed identities.


## Verification

- 306 unit/integration tests passed; Ruff lint/format and mypy (23 files) pass; offline evaluator 23/23.
- Real two-file Grok folder path with --trace passed. Final output: table with paid 1001 and rejected 1002; USD 0.028158 spend, USD 15,000 potential exposure, 19,331 tokens, 49.6 seconds, 0% processing error rate. Trace and metrics totals reconciled directly; no detailed trace on terminal.
- Regression coverage includes zero-call deterministic rejection, partial metrics and trace on interruption, schema-failed billed responses, reasoning normalization, post-commit trace failure, completed metrics surviving stdout failure, candidate identity normalization, duplicate exposure exclusion and Unicode table alignment.
- Review findings resolved; no deferred work. Ready for local commit.
