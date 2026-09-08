# Parallel implementation and integration plan

## Dependency order

A short shared-contract phase is mandatory. Do not launch independent agents to invent their own Invoice models, money helpers, result statuses, or retry semantics.

| Work package | Owns production paths | Owns test paths | Depends on |
|---|---|---|---|
| A — Shared contracts | `invoice_agent/models.py`, `errors.py`, `config.py`, `money.py`, `ports.py`, package init, `pyproject.toml`, lockfile, `.env_example`, `.gitignore` | `tests/unit/test_contracts.py`, `test_config.py`, `test_money.py`, shared fixture factories | None |
| B — Source ingestion | `readers.py`, `ingestion.py`, `normalization.py` | `tests/unit/ingestion/`, `tests/integration/test_ingestion_graph.py` | A; runtime port from C |
| C — LLM runtime | `llm.py` | `tests/unit/test_llm.py`, `tests/live/test_xai.py` | A |
| D — Inventory validation | `tools.py`, `validation.py` | `tests/unit/validation/`, `tests/integration/test_validation_tools.py` | A; runtime C, store port E |
| E — Storage/payment | `database.py`, `identity.py`, `payment.py`, `sql/` | `tests/unit/test_identity.py`, `tests/integration/test_payment_store.py` | A |
| F — VP review | `approval.py` | `tests/unit/test_approval.py`, `tests/integration/test_review_graph.py` | A; runtime C |
| G — Batch/CLI/observability | `runner.py`, `graph.py`, `output.py`, `main.py`, user documentation | `tests/integration/test_batch.py`, `test_cli.py`, `test_observability.py` | A; B–F ports, then implementations |
| H — Evaluation | `scripts/evaluate.py`, `tests/fixtures/expected/`, `tests/integration/test_end_to_end.py` | evaluation tests and failure-injection acceptance tests | A; G integration |

The integration owner owns global `tests/conftest.py`, shared test doubles, and CI configuration. Other agents request fixture additions rather than creating mutually incompatible global fixtures. Agents can create local conftest files under their owned test directories.

## Practical schedule with four available agents

The root/integration owner counts toward the four slots. These are implementation assignments, not a requirement to spawn eight agents at once.

1. **Contract checkpoint:** root completes A with minimal executable models/protocols and one representative contract test. Other agents may read fixtures and design tests without editing shared contracts.
2. **First parallel wave:** one agent handles B; one handles C then F; one handles E then D. Root builds G against frozen ports and starts H's independent test oracles.
3. **Integration wave:** merge working boundaries early. Root composes the real graphs. Agents finish their integration tests and fix owned defects.
4. **Verification wave:** run the complete offline suite, real xAI smoke test, supplied PDF checks, folder demo, then review output and documentation.

Keep the five-hour budget. A–H are ownership slices, not eight sequential milestones. Allocate roughly 30 minutes to contracts, 2.5 hours to parallel modules/early integration, and the remaining time to end-to-end verification and fixes.

## Handoff contract for every module

Each agent returns:

- Owned files changed and public API implemented.
- Test command and actual result, including any skipped checks.
- Dependencies exercised with real implementations versus doubles.
- Failure cases covered and any remaining limitations.
- Contract change requests and implications for consumers.

No agent claims the entire application passes based on its module's tests. No agent edits another owner's files to make a local test pass without coordinating the contract change.

## Contract-change process

A single integration owner approves and applies shared interface changes after notifying consumers. Include a failing cross-module test demonstrating the need. Prefer extending a typed contract to untyped dict compatibility shims. Document approved clarifications in the concerns/decision record.

Keep domain logic independent of CLI, prompts, and SQLite plumbing. Use small injected dependencies rather than a service container. Build only the protocols needed by actual consumers.

## Review checkpoints

- **A accepted:** candidate, findings, LLM envelopes, store/payment ports, output statuses, and retry counters are importable and tested.
- **Vertical slice accepted:** one clean invoice reads, validates via tool call, receives VP critique, and commits payment.
- **Stateful slice accepted:** ingest barrier and date-ordered stock changes verified with real SQLite.
- **Failure slice accepted:** invalid input, model refusal/schema failure, review exhaustion, and payment rollback all produce correct terminal outcomes.
- **Delivery accepted:** checks in testing-and-evaluation.md pass; live check status and limitations are documented.
