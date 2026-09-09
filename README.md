# Galatiq invoice agent

A local multi-agent invoice processor built with Python, LangGraph, xAI Grok, and SQLite. It extracts messy invoices, checks inventory and arithmetic, simulates VP approval with critique and revision, and records mock payments atomically with stock changes.

The original take-home requirements are preserved in [the assignment brief](docs/assignment.md). Architecture and implementation contracts are in [the module specs](docs/specs/spec-invoice-agent/SPEC.md). High-level decisions, alternatives, tradeoffs, and evidence are recorded in the [architecture decision log](docs/decisions/README.md).

## Run locally

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
cp .env_example .env
# Set XAI_API_KEY in .env using your editor.
uv run python main.py --invoice_path=data/invoices/invoice_1001.txt
uv run python main.py --invoice_path=data/invoices --trace
```

You can also install into your own environment with `pip install -e .` and run `python main.py --invoice_path=...`. Development checks additionally require the dev dependencies in `pyproject.toml`.

The default model is explicitly `grok-4.3`, configurable through `XAI_MODEL`. xAI documents that the assignment's original `grok-3` slug now redirects to `grok-4.3`; the application does not silently select another model. See [xAI's retirement notice](https://docs.x.ai/developers/migration/may-15-retirement). The adapter uses the [xAI chat-completions API](https://docs.x.ai/developers/migration/chat-completions) with JSON Schema outputs and actual function calls.

Never commit `.env`. The CLI loads it without changing global environment state; an environment variable takes precedence. `.env_example` contains placeholders only.

## Processing behavior

- Every invocation creates **fresh inventory and a fresh payment ledger** in a unique run directory. Running the same invoice again in a new invocation can simulate paying it again.
- For a folder, all supported files are ingested first. Successfully dated invoices then process sequentially by invoice date, oldest first. Filename and path break ties. Successful payments consume stock for subsequent invoices.
- Supported formats: TXT, JSON, two supplied CSV layouts, XML, and text-based PDF. Folder discovery is non-recursive. Image-only PDFs are explicitly rejected; OCR is not implemented.
- Inventory starts with WidgetA 15, WidgetB 10, GadgetX 5, FakeItem 0. The committed [schema](invoice_agent/sql/schema.sql) and [seed](invoice_agent/sql/seed.sql) reproduce the required inventory.
- Invalid quantities, missing required facts, unknown/out-of-stock/overstock items, unsupported currency, and inconsistent monetary totals block payment. Repeated item quantities are aggregated before checking stock.
- Net terms and quantity are distinct. Invoice 1002 requests 20 GadgetX against stock 5, so it rejects. Its Net 30 terms also disagree with its explicit due date and generate a separate warning.
- Amounts are converted to USD with fixed **mock** rates: USD 1.00 and EUR 1.10. Original amounts, currency, source evidence, and corrections are retained internally for audit. These are not market exchange rates.
- Identical paid invoices in different formats return `already_paid` without another stock change. A changed version of a paid identity rejects. A rejected version does not reserve that identity. The first successful date-ordered version wins; revisions do not automatically supersede originals.

The inventory depletion follows the assignment's simulation rules; it does not model supplier receiving/accounting.

## Agents and safeguards

```text
Read/map or extract -> source checks -> bounded correction -> ingestion barrier
Date-order loop:
  identity -> review invoice -> final payment gate -> SQLite transaction
                | inventory tool -> deterministic validation
                | blockers: reject immediately, skip VP
                | eligible: VP proposal -> critique -> bounded revision
```

Known JSON/XML/CSV structures map deterministically. Grok interprets TXT/PDF and unfamiliar valid structured layouts with field evidence. The validation agent actually calls `lookup_inventory`; SQL facts and independent coverage checks determine whether every item was validated.

One combined review node coordinates inventory validation and VP review. Hard blockers reject immediately with deterministic reasons, including above USD 10,000; the audit explicitly records that VP review was skipped. Otherwise, one VP persona produces a proposal and reviews it in a separate model call. Eligible invoices above USD 10,000 require explicit enhanced checks, whether the VP ultimately approves or rejects. Up to two revisions are allowed. Mechanical safeguards enforce blockers and checklist completeness even if the proposal and critique both incorrectly approve.

A semantic fingerprint identifies equivalent payable content. A separate full candidate digest binds the approved facts to validation. Payment revalidates the evidence, rechecks current stock, and writes ledger/stock in one transaction. The payment function is a **local mock with no bank connection or external side effect**.

Retries are bounded separately: three extraction attempts, three VP cycles, two inventory tool rounds, and up to three transport attempts per model request. Source defects yield ordinary rejection; provider and infrastructure failures yield structured operational errors.

## Results and observability

The default CLI prints brief, filename-prefixed progress to stderr and decisions and a short summary to stdout. Messages are flushed immediately. Invoices are read first, then reviewed oldest first so stock goes to earlier orders. Routine internal review steps are kept in the activity log.

```text
invoice_1001.txt: Reading invoice (1/20)
...
Reviewing oldest invoices first so stock goes to earlier orders.
invoice_1001.txt: Reviewing invoice dated 2026-01-01
invoice_1001.txt: Paid (simulated) — $5,000.00 USD
invoice_1002.txt: Rejected; no payment made
invoice_1002.txt: Reason: WidgetB requires 20, available 5
```

Rejected invoices show up to two distinct reasons, with concrete validation problems first. Outcomes also show up to one warning. Long explanations are shortened, and additional details are available in the saved report. Successful payments do not repeat approval explanations.

Use `--json` for JSONL stdout (one invoice result plus a final summary); progress remains on stderr, so `--json > results.jsonl` is safe for scripts. Business rejection exits 0; operational failure exits 1; invalid input/configuration exits 2.

Detailed events are saved continuously to `runs/<run_id>/trace.jsonl`, including model timing, token usage, inventory tools, corrections, and VP critique. They are never printed to the terminal, even with `--trace --json`. `--trace` additionally embeds events in `results.jsonl`; ordinary progress and rejection reasons remain visible without it. Credentials and hidden model reasoning are not logged.

Every completed run ends with outcome counts, the simulated payment total, and elapsed time. The full invoice table and technical metrics are available in `report.html` and `metrics.json`; `--json` includes metrics in its final summary. These include estimated API spend, blocked payment exposure (**not realized savings**), token counts, latency, and error rates. Interrupted runs retain the trace and save explicitly partial metrics when storage is writable. See [metric definitions and pricing](docs/metrics.md).

Each invocation writes `runs/<run_id>/inventory.db`, `results.jsonl`, `audit.jsonl`, `trace.jsonl`, and `metrics.json`; use `--output_dir` to select a different root. Completed outcomes are flushed incrementally, so interruption preserves earlier results. The audit file retains source hashes, exact normalized candidates, source amounts/evidence, validation reports, and bound VP decisions. The run directory is printed before processing so a partial run can be reconciled against its ledger. These are ignored local artifacts, not shared state for future runs. A failed final inventory read is reported as unavailable, not fabricated as the seed balance.

## Verification

```bash
uv run pytest tests/unit tests/integration -q
uv run pytest tests/unit tests/integration --cov=invoice_agent --cov-branch --cov-report=term-missing
uv run ruff check invoice_agent main.py tests scripts
uv run ruff format --check invoice_agent main.py tests scripts
uv run mypy invoice_agent main.py
uv run python scripts/evaluate.py
```

Offline tests use scripted model responses at the provider boundary, with real readers, actual LangGraph execution, real SQLite files/transactions, and CLI serialization. The evaluator distinguishes isolated fixtures from stateful folder processing. Expected business rejections count as evaluation passes; mismatched expectations fail evaluation.

Live verification requires the local xAI key:

```bash
RUN_LIVE_XAI=1 uv run pytest tests/live -q
uv run python scripts/evaluate.py --live
uv run python main.py --invoice_path=data/invoices --trace
```

A skipped live check is not proof of provider compatibility. See [build status and evidence](docs/build/status.md) for actual results and independent review findings.

## Scope and limitations

This prototype does not verify legal vendor identity, prove fraud, read image-only scans, make real payments, or guarantee arbitrary free-text extraction accuracy. Evidence checks catch concrete errors and retries remain bounded. Late extraction inconsistencies reject rather than reorder invoices after payments have begun.

The clean examples in the assignment pass fresh-stock validation in isolation; a folder may reject them after earlier invoices consume inventory. No cloud deployment, production banking integration, or cross-run payment protection is claimed.

Verified live commands and observed results: [Live examples](docs/live-examples.md).
