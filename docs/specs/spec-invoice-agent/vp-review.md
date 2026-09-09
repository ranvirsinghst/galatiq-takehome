# F — Automated VP review and reflection subgraph

> Current policy update (2026-09-08): the processing graph has one combined review node. Inventory tools and deterministic checks run first; hard blockers (including on high-value invoices) reject immediately with no VP calls. Eligible invoices retain the VP proposal/critique/revision rules below. See the [architecture decision log](../../decisions/README.md) for rationale and superseded behavior.

## Boundary

Own `approval.py` and review tests. Implement one VP persona with propose, critique, and revise phases. Consume canonical invoice facts and a deterministic validation report. The agent has no payment tool and cannot mutate source data, stock, or findings.

Use a real LangGraph review subgraph exposed through the shared `review` function. Multiple nodes represent phases of the same persona, not a requirement for separate VP/critic identities.

## Decision and critique contract

Proposal includes approved/rejected, concise reason, referenced finding codes, and structured checklist evidence. Critique is a separate model call receiving the proposal plus the same immutable facts/report and explicit review checklist. It returns accept/revise with concrete issues.

Checklist:

- All blockers imply rejection, including source findings carried from ingestion.
- All warnings are acknowledged, even when they do not prevent payment.
- Cited finding codes exist; no invented verification or allegations.
- Decision and rationale agree.
- For total USD > 10000, explicit arithmetic, aggregate stock, data completeness, and supported suspicious-signal assessments are present.
- Incomplete arithmetic checks are acknowledged, never represented as checks that passed.

Enforce mechanically checkable parts in code after model critique: a critique saying accept cannot authorize approval with blockers, missing high-value checklist entries, invalid evidence IDs, or unacknowledged warnings. These failures route to revision under the same budget. Semantic quality remains model-dependent; deterministic checks do not prove the prose is correct.

One initial propose/critique pair, up to two revise/critique pairs. Maximum six semantic model calls for a fully exhausted normal review. Transport retries do not increase revision count. A model failure to produce valid response structure is classified under the shared error taxonomy; it never defaults to approval.

Schema-failure routing is part of that same three-cycle budget. Each cycle makes one proposal/revision call; if it parses, make at most one critique call. If either response is schema-invalid, consume the cycle and supply the parse errors to the next cycle's proposal/revision. Skip critique when no valid proposal exists. Use the previous valid proposal when available as revision context; otherwise regenerate a proposal. No extra schema-only semantic calls are allowed. On the last cycle, invalid response structure yields operational `LLM_SCHEMA_ERROR`; a valid critique that still requests revision or fails deterministic checks yields rejected `REVIEW_EXHAUSTED`. Track cycle index even if a missing critique makes the number of actual calls less than six.

ReviewOutcome binds the accepted proposal to A's full candidate digest and the validation report. The payment-equivalence fingerprint is not sufficient for this purpose because it excludes warning-relevant optional fields.

On unresolved review after the limit, reject with `REVIEW_EXHAUSTED` and outstanding issues. An accepted reject returns a normal rejection outcome with no payment request. Accepted approval remains subject to G/E's final gate.

## Unit tests

Use scripted responses through the shared LLM port:

- Clean low-value invoice: proposal then critique accepted, exactly two calls.
- Valid synthetic high-value invoice: enhanced checklist present, can approve.
- Threshold exactly 10000 versus 10000.01 selects the correct checklist.
- Proposal approves despite stock blocker; critique requests revision; corrected rejection accepted.
- Critique incorrectly accepts blocked approval: deterministic guard still refuses.
- Critique incorrectly accepts incomplete high-value review: revision occurs.
- Endless revise: exactly two revisions, six normal calls, rejection with reasons.
- Unknown finding references or unsupported fraud assertions are challenged.
- Terms warning acknowledged without silently changing due date.
- Model refusal/schema/transport failure never yields an approved default.
- Invalid first proposal followed by valid second-cycle proposal/critique succeeds with three calls; invalid critique followed by repaired proposal/critique consumes two cycles; mixed schema/revision exhaustion never exceeds three cycles or six calls and selects the documented final error/rejection class.

## Integration tests

Compile and invoke the actual subgraph, not a mocked `review()` function. Assert visited phase events and final outcome. Connect real validator findings for negative quantity and unknown item. Connect one accepted outcome to the actual final payment gate and prove only the clean case reaches the mock.

Events contain proposal/critique/revision summaries, phase and attempt, finding references, and elapsed duration. Do not ask the model for hidden chain-of-thought or expose complete prompts in user traces.
