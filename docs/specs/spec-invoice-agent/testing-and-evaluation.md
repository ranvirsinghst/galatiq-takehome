# H — Rigorous testing, integration gates, and batch evaluation

## Test design principles

Tests assert business invariants and module boundaries, not an implementation's exact prompt wording or private helper call sequence. Use literal independently computed expected quantities/money/findings. Do not generate expected results by calling the same calculation or fingerprint function under test.

Default tests are hermetic: no credentials, network, current-date dependence, repository-root database, or mutable global fixtures. Use tmp_path for files/databases; inject clocks, ID factories, sleepers, model transport and payment mock. Tests should run in any order and leave supplied fixtures unchanged.

Unit tests cover deterministic decisions and adapters. Contract tests validate shared model and port compatibility. Integration tests use actual graphs, real readers, real SQLite transactions, and real serialization with only external LLM transport replaced. Live tests independently exercise real xAI capabilities. A stack of mocks that replaces all nodes does not count as integration.

## Test suite layout

```text
tests/
  conftest.py                 # integration owner only
  doubles.py                  # scripted LLM implementing shared port
  factories.py                # valid minimal domain objects, explicit overrides
  unit/                       # module owners
  integration/                # real module composition and temporary SQLite
  live/                       # opt-in xAI, isolated marker
  fixtures/
    expected/                 # manually reviewed field/finding oracle records
    synthetic/                # targeted edges absent from supplied data
scripts/evaluate.py
```

A scripted model should queue responses by phase and validate expected tool-message structure. It should fail loudly on unexpected extra calls. It must not decide whether invoices are valid or reproduce production policy; tests specify its responses.

## Minimum check commands

Integration owner configures tested tools and documents exact commands, targeting:

```bash
python -m pytest tests/unit tests/integration -q
python -m pytest tests/unit tests/integration --cov=invoice_agent --cov-branch --cov-report=term-missing
python -m ruff check .
python -m ruff format --check .
python -m mypy invoice_agent
python scripts/evaluate.py
RUN_LIVE_XAI_TESTS=1 python -m pytest tests/live -q
```

Exclude vendored `_bmad/scripts` from application formatting/lint/type/coverage gates; do not reformat upstream helpers. Use explicit dependencies and a reproducible lockfile. API calls are opt-in; CI offline gate must never silently consume live credentials.

Target at least 90% branch coverage for authored payment, identity, validation, and routing logic and at least 80% for the application overall. These are proposed engineering gates, not a substitute for the named invariant tests. Missing branches in financial-state guards require explanation and tests even if the percentage passes. Avoid meaningless tests to raise coverage; no test-only bypasses of domain policy.

## Cross-module acceptance matrix

| ID | Scenario | Required assertions |
|---|---|---|
| INT-01 | Clean single input | Tool called; proposal and critique distinct; one payment; exact stock/ledger/output |
| INT-02 | Folder ingestion barrier | Every ingestion terminates before first lookup/review/payment |
| INT-03 | Date ordering | Older invoice processed first despite filename; corrected date used; ties stable |
| INT-04 | Stateful depletion | Two individually valid demands cause later rejection in shared run |
| INT-05 | Fresh invocation | Two runs using same output location start seeds/empty ledger independently |
| INT-06 | Equivalent formats | 1011 TXT/PDF equivalence yields one new payment, one already_paid |
| INT-07 | Revised paid invoice | Original 1004 then R1: version conflict, no incremental payment |
| INT-08 | Rejected original | No successful ledger reservation; valid later version can be evaluated |
| INT-09 | Tool incompleteness | Missing item recovered within budget or error; fabricated prose cannot replace SQL |
| INT-10 | Hostile model approval | Both proposal/critique approve a blocker; deterministic guard prevents payment |
| INT-11 | High-value path | Valid >10000 synthetic invoice passes enhanced review; missing checklist cannot pay |
| INT-12 | Reflection exhaustion | Maximum two revisions, rejection with outstanding issues |
| INT-13 | Extraction recovery | Source-supported omission corrected before sorting; exhausted repair rejects |
| INT-14 | Genuine bad source | No invented vendor/quantity/date; informative rejection survives model serialization |
| INT-15 | Payment failure | Mock exception/false result and DB faults roll back ledger and stock |
| INT-16 | Commit observability | No paid event/output before successful commit |
| INT-17 | Batch error isolation | One bad file doesn't suppress others; shared fatal error marks remaining inputs |
| INT-18 | Trace/summary | Valid JSONL, redaction, stable reasons, paid USD excludes duplicates |
| INT-19 | Late extraction issue | Reject without reopening ingestion or changing committed ordering |
| INT-20 | Actual PDFs | Real library reads supplied PDFs and retains required evidence |

## Failure injection

Cover request timeout/429/5xx; auth failure; malformed/truncated/refused model response; wrong tool name/call ID/arguments; missing lookup coverage; invalid date/token; file I/O failure; source parse failure; mock exception; constraint violation; failed stock update; failed commit; and unusable rollback/connection.

Assert typed outcome, exact retry bounds, correct exit classification, and unchanged state where required. Fault injection belongs at narrow external boundaries, never by disabling payment eligibility checks.

For invalid approvals, tests must exercise both a critique that catches the error and a critique that incorrectly accepts it. The latter verifies that reflection is not the only payment control.

## Fixture evaluation

`evaluate.py` has two named suites:

- **Isolated fixtures:** each input gets a new seed/ledger; compare normalized fields and finding codes against independent expectations.
- **Stateful folder scenarios:** shared seed once, all ingestion first, date-ordered outcomes and final balances.

Routine evaluation uses recorded/scripted extraction outputs for the model boundary and actual deterministic code. It explicitly labels this mode. A live option, e.g. `--live`, uses xAI and reports its results separately. No invoice ID based branching is allowed in production.

The evaluator emits per-case pass/fail with field/code diffs and a nonzero exit when expectations fail. Its exit status differs from runtime CLI: expected business rejection can be an evaluation pass. Never overwrite golden expectations automatically from observed output.

Supplied-fixture baseline expectations:

| Input | Minimum fresh-state facts/findings |
|---|---|
| 1001 | WidgetA 10, WidgetB 5, total USD 5000; stock/arithmetic pass |
| 1002 | GadgetX 20; stock blocker; Net 30/date warning; amount 15000 |
| 1003 | FakeItem zero-stock blocker; unresolved relative due date; no claim of proven fraud |
| 1004 original | WidgetA 3, WidgetB 2; USD 1890; stock/arithmetic pass |
| 1004 revised | Added GadgetX 5; USD 5940; conflict only after paid original |
| 1005 | GadgetX 8 exceeds fresh stock 5 |
| 1006 | Two CSV field/value item groups; USD 2750; stock/arithmetic pass |
| 1007 | WidgetA 20 and WidgetB 15 exceed stock; CSV footer parsed |
| 1008 | SuperGizmo and MegaSprocket unknown |
| 1009 | Negative quantity, blank vendor, absent due date, invalid total |
| 1010 | WidgetA aggregate 12; shipping 150; USD 7185; preserve rush price |
| 1011 TXT/PDF | Same payment semantics; USD 3000; equivalent despite optional fields |
| 1012 TXT/PDF | OCR-like normalizations with evidence; USD 9975 |
| 1013 JSON/PDF | Repeated items exceed stock; verify actual PDF and USD 50 discrepancy |
| 1014 | EUR 4125 -> USD 4537.50 under mock rate |
| 1015 | Table CSV plus summary rows; USD 6500 |
| 1016 | WidgetC unknown |

These are minimum expectations, not an exhaustive restriction on additional correct findings. Inspect actual fixture content to complete expected manifests; do not assume generation script and PDF bytes are identical.

## Review and delivery evidence

Each module owner runs relevant unit/integration checks. Integration owner runs the complete gates after composition and reports actual commands, counts, failures/skips, coverage gaps, and live status. Add CI for the offline suite if feasible within budget.

If a live extraction or tool test fails, fix or document the concrete limitation; do not count the offline fake as proof of live behavior. Do not weaken financial guards to make supplied samples appear clean.
