# Galatiq Take-Home — Agent Handoff Brief

> Historical context: the build-ready [implementation specification](galatiq-invoice-agent-spec.md) supersedes the architecture draft and open questions below, incorporating the subsequent user decisions.

Context handoff for continuing work on Ranvir's Galatiq (Forward Deployed Engineer) take-home assignment: a multi-agent invoice-processing system.

## Assignment

- **Repo**: https://github.com/galatiq-ai/galatiq-case-invoices
- **Submission**: public GitHub repo link (GitHub only)
- **Premise**: Acme Corp (PE-backed manufacturer) loses $2M/yr on manual invoice processing — 30% error rate, 5-day delays. Build a working multi-agent prototype (not slides/designs) that automates it end-to-end.

### Required workflow (4 stages)

1. **Ingestion** — extract Vendor, Amount, Items (with quantities), Due Date from invoice documents. Inputs are messy: PDFs, text, CSV, JSON, XML, with typos, missing data, and potentially fraudulent entries.
2. **Validation** — check extracted data against a mock SQLite inventory DB. Flag: quantity exceeding stock, items not in inventory, other data integrity issues (e.g. negative quantity).
3. **Approval** — rule-based VP-review simulation (e.g. invoices over $10K need extra scrutiny). Must include a **reflection/critique loop** — the agent reasons through approve/reject, not a single-pass decision.
4. **Payment** — call `mock_payment(vendor, amount)` if approved; log rejection with reasoning if not.

### Technical requirements

- LLM: xAI Grok as core reasoning engine (`https://grok.x.ai`, model `grok-3`); other models acceptable if no API key.
- Multi-agent orchestration: LangGraph, CrewAI, AutoGen, or custom — framework choice is open.
- Must demonstrate: function calling / tool use, structured outputs, self-correction loops.
- Runtime: no internet for *external business* APIs — inventory DB and payment must be simulated locally (this does not necessarily preclude calling the LLM provider itself).
- Stack: Python, libs like `langchain`/`crewai`/`autogen`/`pdfplumber`/`PyMuPDF`. Runs locally, no cloud deploy.
- CLI entry point: `python main.py --invoice_path=data/invoices/invoice1.txt`, structured logs/results as output.

### Provided test data (`data/invoices/`)

Mixed formats (txt, csv, json, xml, pdf) across invoices 1001–1016, with deliberately broken cases to exercise validation:

| Invoice | Issue |
|---|---|
| 1001, 1004, 1006 | Clean — should pass validation |
| 1002 | Requests 20× GadgetX, only 5 in stock → stock mismatch |
| 1003 | References FakeItem (0 stock) → fraud/out-of-stock flag |
| 1008, 1016 | SuperGizmo/MegaSprocket/WidgetC not in DB → unknown item |
| 1009 | Negative quantity → data integrity flag |

Starter inventory schema (`inventory.db`, SQLite): table `inventory(item TEXT PRIMARY KEY, stock INTEGER)`, seeded with WidgetA(15), WidgetB(10), GadgetX(5), FakeItem(0). Can be extended with price/category columns for richer validation.

### Evaluation criteria

Functionality (end-to-end), code quality (clean, testable, error handling, observability), agentic sophistication (LLM integration, multi-agent flow, tool use, self-correction), shipping mindset (MVP under ambiguity, ruthless scope cuts), presentation (business translation), above/beyond (extra features, expanded test cases), UI/UX (usable, pleasant to run).

## Decisions made so far

**Framework: LangGraph** (over AutoGen or plain LangChain). Rationale:
- The task is a fixed, deterministic 4-stage pipeline with a hard branch (approve → payment, reject → log) — LangGraph's explicit state graph + conditional edges map directly onto that, more naturally than AutoGen's conversational/group-chat model.
- Plain LangChain chains are DAG-only (single pass through); LangGraph natively supports **cycles**, which is what the approval stage's reflection/critique loop needs (a critique node routes back to the approval node to revise, capped by a retry counter).
- LangGraph's shared state object and optional checkpointer give easy per-stage inspectability/persistence, which serves the "code quality / observability" eval criterion directly.
- CrewAI was considered as a middle ground (role-based agents, less flexible for custom loop logic); ruled out in favor of LangGraph's finer control.

## Architecture drafted (not yet implemented)

**State schema** (TypedDict): `raw_invoice`, `extracted` (vendor/amount/items/due_date), `validation_flags`, `approval_decision`, `approval_reasoning`, `critique_count`, `payment_result`.

**Nodes**: `ingestion_node` (LLM extraction), `validation_node` (SQLite lookup + flagging), `approval_node` (proposes decision + reasoning), `critique_node` (checks the reasoning, e.g. correct application of the >$10K rule), `payment_node`, `rejection_node`.

**Edges**:
- `START → ingest → validate → approval → critique` (linear)
- Conditional edge after `critique`: `revise` → back to `approval` (increment `critique_count`, capped at ~2 passes) | `final` → proceed to decision routing
- Conditional edge on `approval_decision`: `approved` → `payment_node` → `END` | `rejected` → `rejection_node` → `END`

**Not yet decided**: whether Ranvir has xAI Grok API access or will substitute another model (OpenAI/Anthropic/local) for the LLM calls — this affects the client-wrapper structure and was the open question at the point this brief was written. Also not yet started: concrete file/module layout, the extraction prompt/parsing logic for each input format (txt/csv/json/xml/pdf), the actual SQLite validation code, and the CLI entry point (`main.py`).

## Useful context

Ranvir has a background in agentic AI systems, MCP servers, and RAG/context-engineering — comfortable with framework internals, wants specific/grounded technical reasoning rather than generic explainers. This is a take-home for Galatiq's Forward Deployed Engineer role (company background, interviewer, and process notes are tracked separately and aren't essential for implementation work).
