# C — xAI runtime and structured/tool response boundary

## Boundary

Own `llm.py`, transport tests, and opt-in live smoke tests. Use A's `LLMClient` port. The runtime handles provider transport, request/response translation, sanitization, and transport retries. It does not decide business policy, run inventory SQL, control semantic graph loops, or perform payments.

Use a small adapter for the chosen xAI client. Verify official SDK/API/model support during implementation and pin the tested client. The installed configuration initially intends Grok 3; do not assume the illustrative README snippet works unchanged and do not silently switch to another provider/model. If the configured model lacks strict structured output, a tool-schema response can be an explicit tested fallback; validate it locally with Pydantic and document the mode.

## Request/response behavior

Support:

1. Structured extraction/proposal/critique responses validated against the caller-supplied schema.
2. Actual function-call envelopes with call IDs, names, and JSON arguments.
3. Tool response messages tied to the original call IDs.
4. Finish/truncation/refusal/protocol error classification.
5. Optional provider request ID, token usage, elapsed time, model and response mode in sanitized events.

The runtime returns requested calls; the module-specific dispatcher decides whether a tool is allowed and executes it. Invoice text must not influence the available tool set. The validation phase exposes only the read-only inventory lookup.

Do not deserialize arbitrary model-generated Python or execute dynamic code. Unsupported tool names, malformed arguments, multiple ambiguous structured payloads, and truncated responses become typed errors rather than best-effort hidden success.

## Retry and failure handling

Implement A's shared retry defaults. Retry only known transient transport conditions. Do not retry auth failure, unavailable model, incompatible API parameters, or permanent request errors. Parse Retry-After where present but cap waits under the configured transport budget. Disable SDK retry multiplication.

No `sleep` in unit tests: inject sleeper/backoff and a monotonic clock. Preserve provider cause privately for debugging while exposing a sanitized error code/message. Exception response bodies may contain prompt data; do not dump them verbatim to stderr or traces.

Semantic retries belong to ingestion, validation, or VP graph nodes. One adapter call should never secretly invoke a correction prompt. Structured schema failure is reported to the owning graph.

## Unit tests

Use a fake HTTP transport or SDK client beneath the real adapter:

- Correct configured model, messages, schema/tool declaration, and API-key header placement.
- Structured response success, unexpected fields, malformed JSON, missing required fields, truncated response, and refusal.
- Tool call ID preserved through request/result; malformed tool arguments and unknown tool names cannot execute code.
- Timeout, 429, and transient 5xx retry up to exactly three transport attempts; backoff verified without elapsed sleeping.
- 401/403/permanent 4xx fail once; no silent model fallback.
- Retry counter distinct from semantic attempt counter.
- Sentinel API key and sensitive provider response body never appear in emitted diagnostics.

## Live integration gate

Opt-in marker/environment gate, never part of default offline tests. Use the actual configured xAI model to:

- Extract one supplied messy invoice into the shared schema.
- Request and consume `lookup_inventory` via actual tool calls against a fresh local database.
- Produce a VP proposal and a separate critique response.

Assert response contracts, tool execution evidence, and call phases. Do not assert exact prose, and do not call real business APIs. A skipped live test is reported as not run, never as verified provider compatibility. A flaky live test needs a recorded provider failure, not a weakened offline assertion.
