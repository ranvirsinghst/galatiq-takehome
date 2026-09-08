# A — Shared contracts and configuration

## Responsibility

Define the minimum shared Python models, error taxonomy, dependency ports, policy configuration, and testing foundations. This is the first implementation checkpoint and has one owner. Preserve the fields in the overall spec; refinements below prevent parallel modules from disagreeing at boundaries.

## Model layers

Use Pydantic boundary models and TypedDict graph state. Separate these layers:

1. `SourceDocument`: source ID/path/format/hash, raw representation, evidence references, reader warnings.
2. `InvoiceCandidate`: extracted facts with provenance, nullable required fields, original invalid tokens, source amounts, canonical USD amounts when conversion succeeds, corrections/assumptions, and field-level parse findings.
3. `ValidationReport`: complete findings, aggregate valid quantities, stock snapshot, performed/unavailable checks, full frozen-candidate digest, inventory generation.
4. `ReviewOutcome`: final proposal, critique, revision count, accepted flag, rejection reasons when exhausted.
5. `PaymentRequest`: only constructible from a complete positive payable invoice, complete validation, and accepted approval. Contains run ID, identity/fingerprint, USD amount, aggregate quantities, source ID, and immutable candidate/report/review evidence snapshots. Those snapshots make the payment boundary's checks executable rather than relying on a caller-supplied `approved=True` assertion.
6. `InvoiceResult` / `RunSummary`: stable public output contracts.

A model parser must not erase invalid invoice facts. Preserve a raw quantity token such as `-5`, `2.5`, `abc`, or missing separately from its optional parsed Decimal value. Boolean values must not pass as integer quantities. Likewise preserve malformed monetary/date tokens rather than throwing away an entire candidate. A structurally invalid LLM response is different from a valid response describing invalid source data.

`PaymentRequest` construction validates domain preconditions again. It is internal, not an LLM-writable authority token. Payment code must still enforce conditions and fresh stock at its own boundary.

## Shared values

- Enums for business decisions, execution status, payment status, finding severity/origin, error codes, and trace event names.
- Money: finite Decimal values, cents serialization as strings; preserve source decimal precision for arithmetic before conversion.
- A owns `money.py`: pure finite-Decimal parsing, cents rounding/serialization, and fixed-rate conversion helpers. Source-specific token normalization stays with B; arithmetic policy checks stay with D.
- Dates: `date` values when valid, raw strings always available; no implicit use of today's date for fixture policy.
- Evidence: deterministic ID, source ID, location (page/row/path), literal source excerpt when available.
- Normalization: field, original value, normalized value, method, evidence IDs; no silent replacement.
- IDs: run-scoped source IDs plus opaque run/payment IDs from injected factories where needed for deterministic tests.
- Digests: A owns `candidate_digest(candidate)` over all immutable extracted/review-relevant fields, including payment terms, assumptions and evidence references, excluding runtime timestamps/events. This is distinct from E's payment-equivalence fingerprint, which intentionally omits presentation-only fields. Bind validation, review, and payment to the full digest; use the equivalence fingerprint only for duplicate/version decisions.
- Inventory snapshot: `{item: stock_or_missing}`, run ID, monotonically increasing inventory generation.
- Finding order: stable by validation rule, then item/line; never compare generated prose as a test oracle.

## Ports to freeze before parallel work

The signatures below are the intended callable contract. Owner A materializes the named result/request models in `models.py` and protocol definitions in `ports.py`; implementations must not substitute loosely shaped dictionaries. Small naming changes are allowed only at the contract checkpoint.

```python
class LLMClient(Protocol):
    def complete(self, request: LLMRequest) -> LLMResponse: ...

class InventoryReader(Protocol):
    def lookup(self, items: list[str]) -> InventorySnapshot: ...

class PaymentStore(InventoryReader, Protocol):
    def find_paid(self, identity: InvoiceIdentity) -> PaidRecord | None: ...
    def pay(self, request: PaymentRequest, mock: MockPayment) -> PaymentOutcome: ...
    def snapshot(self) -> InventorySnapshot: ...
    def close(self) -> None: ...

class EventSink(Protocol):
    def emit(self, event: TraceEvent) -> None: ...
```

Cross-module functions:

```python
read_source(path: Path, source_id: str) -> SourceDocument
ingest(source: SourceDocument, llm: LLMClient, policy: Policy, events: EventSink) -> IngestionOutcome
validate(candidate: InvoiceCandidate, snapshot: InventorySnapshot, policy: Policy) -> ValidationReport
validate_with_tools(candidate: InvoiceCandidate, inventory: InventoryReader, llm: LLMClient, policy: Policy, events: EventSink) -> ValidationOutcome
review(candidate: InvoiceCandidate, report: ValidationReport, llm: LLMClient, policy: Policy, events: EventSink) -> ReviewOutcome
payment_identity(candidate: InvoiceCandidate) -> InvoiceIdentity | None
payment_fingerprint(candidate: InvoiceCandidate) -> str | None
build_payment_request(candidate: InvoiceCandidate, report: ValidationReport, review: ReviewOutcome, run_id: str) -> PaymentRequest
run(paths: list[Path], dependencies: RunDependencies) -> RunResult
```

Ingestion outcome is either candidate-with-findings, terminal source rejection, or operational failure. Candidate-with-findings may be invalid for payment but must be sortable if its invoice date is valid. Reject a source-missing/invalid date before sorting. Preserve all known source findings on terminal results.

Validation graph orchestration and tool execution belong to D; `validate` itself is pure and never calls the model or database. Review's implementation is the compiled bounded subgraph owned by F. Batch composition belongs to G.

D owns `validate_with_tools`, returning a report or typed operational failure. E owns `payment_identity`, `payment_fingerprint`, and `build_payment_request`. Missing identity or facts insufficient for fingerprinting return None and cannot take the duplicate shortcut; normal validation/rejection handles the candidate. G calls these exported E helpers rather than reproducing identity rules. E verifies request snapshots, derived digest/amount/quantities, accepted critique and deterministic eligibility before its transaction; it does not rerun LLM judgment.

`LLMRequest` includes phase, messages, optional output schema, available tool schemas, and configured output budget. `LLMResponse` has typed structured content or tool calls, finish reason, optional usage, and sanitized provider request ID. Never couple downstream modules to vendor SDK classes.

## Error taxonomy

| Condition | Representation | Retry | Batch behavior |
|---|---|---|---|
| Invalid/missing source business data | Findings; rejected business decision | Only if demonstrably misextracted | Continue |
| Malformed document / unreadable PDF contents | `SOURCE_PARSE_FAILED`, rejected | No repeated deterministic parsing | Continue |
| File disappears / permission denied | `SOURCE_IO_ERROR`, operational error | No default retry | Continue other readable files |
| LLM response violates schema | `LLM_SCHEMA_ERROR`, recoverable phase issue | Owning semantic loop, bounded | Error after exhaustion unless phase has a valid unresolved review |
| Valid critique requests endless revision | `REVIEW_EXHAUSTED`, rejection | Two revisions | Continue |
| Extraction still omits source-supported fields | `EXTRACTION_EXHAUSTED`, rejection | Two corrections | Continue |
| Timeout, transient rate limit/server failure | `PROVIDER_TRANSIENT`, operational | Two transport retries | Error on exhaustion |
| Invalid auth/config/model capability | Config/provider permanent error | None | Stop shared provider work; mark affected inputs as errors |
| Missing/invalid tool evidence after bounded recovery | `TOOL_PROTOCOL_ERROR`, operational | Two tool rounds | Continue if isolated; never pay |
| Mock payment failure | Operational error; `payment=failed` | None | Continue if database remains healthy |
| Shared DB corruption/unusable transaction state | Run infrastructure error | None | Stop processing; report remaining inputs as unprocessed errors |

Do not catch every exception and turn it into a rejection. Catch known exceptions at module boundaries and preserve sanitized cause/code. At the outer CLI boundary an unexpected exception produces an internal-error result, nonzero exit, and diagnostic trace; never a false successful summary.

## Retry budgets

- Extraction: initial candidate plus two correction attempts, maximum three semantic attempts.
- VP: initial proposal plus initial critique; at most two revise/critique pairs, maximum six calls.
- Inventory tools: at most two model tool-request rounds; tool execution itself has no hidden retry.
- Transport: at most two retries per model request, maximum three transport attempts; no automatic retry on authentication, unsupported schema, or unavailable model.
- Default per-request timeout: 30 seconds. Backoff: bounded exponential delay with jitter, injected sleeper/random source in tests. Disable any SDK automatic retries or account for them within this same budget.

Do not let framework recursion limits become business retry policy. Record semantic attempts and transport retries separately. These are proposed implementation defaults and configurable centrally.

For VP schema errors, use exactly the three-cycle budget described in vp-review.md: one proposal/revision call plus at most one critique per cycle. A schema-invalid response consumes that cycle, without opening another response-repair loop. Final schema-invalid cycle is operational error; final structurally valid but unresolved critique is rejection.

## Observability and configuration

`Policy` is immutable and injected: fixed FX map, USD threshold, source arithmetic tolerance, aliases, retry counts. Load `.env` only in the application boundary, not at module import. Library unit tests run with no API key and no local `.env` dependence.

`TraceEvent`: schema version, run/source IDs, per-run increasing sequence, stage/event, elapsed duration or timestamp from injected clock, severity, bounded payload, optional error code. Prompts, credentials, and complete raw invoice text are not default event payloads. Include concise evidence/rationale and tool request/results for `--trace`.

## Unit acceptance

- Candidate preserves negative/fractional/nonnumeric/boolean/missing tokens; eligible payment model rejects each.
- Decimal roundtrip through JSON preserves cents, non-finite inputs fail safely, enum/status combinations cannot contradict one another.
- `status=error` has a structured error and no fabricated business rejection; paid requires approved/completed and a payment ID.
- Policy defaults and env override validation work without mutating process-global state.
- Event serialization redacts a sentinel credential and preserves IDs.
- Runtime/store test doubles implement these same ports; no production module imports test helpers.
