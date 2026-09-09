# Design review action items

Outcome of a design review pass over the delivered prototype on 2026-09-08, checked against the [assignment brief](assignment.md) and the committed code. This file records what the review confirmed as already delivered, what it found missing, and which suggestions were deliberately declined. Accepted architectural changes are promoted to the [decision log](decisions/README.md); this file is the working list, not the authority.

## Already delivered

These were raised in review and verified present in the code. No further work.

| Point raised | Where it lives |
|---|---|
| Fresh, stateless inventory per invocation; no cross-run memory | `main.py` run-directory setup; every invocation creates `runs/<run_id>/inventory.db` |
| Merge inventory validation and VP approval into one node, with deterministic rules checked first | `invoice_agent/graph.py` `combined_review`; hard blockers reject and skip the VP entirely |
| Per-step user-facing messages rendered in the terminal | `invoice_agent/console.py` `ConsoleReporter.event` |
| Turn the assignment's scenario table into a runnable eval set to catch regressions | `scripts/evaluate.py` with `tests/fixtures/expected/outcomes.json`; 20 isolated cases plus 3 stateful scenarios |
| Keep the toolset minimal | Two typed tools: `lookup_inventory` and `lookup_price` |
| Maintain a decision log for reviewers | [decisions/README.md](decisions/README.md) |
| Lead with the single-invoice path | README's first documented command processes one `.txt` |

## Open items

### 1. Catalog price validation — delivered 2026-09-09

At the design review, the system had no notion of what an item *should* cost. `validation.py` confirms that quantity times unit price equals the line total, but never compares unit price to any reference, so an inflated price that is internally consistent passes silently.

Concrete miss in the supplied data:

```
data/invoices/invoice_1010.txt:14
WidgetA (rush order)        4      $300.00     $1,200.00
```

A 20% overcharge on a $250 item. The pre-feature `outcomes.json` expected `invoice_1010.txt` to be **approved with no findings**. Pricing is the one fraud vector the brief explicitly names that the prototype cannot currently see, and the brief invites the extension directly: "If you want your system to also validate pricing or vendor information, consider adding tables for those as well."

**Delivered:** Typed `lookup_price` evidence, per-line Decimal tolerance checks, independent transactional catalog verification, CLI/HTML explanations, and regression coverage are implemented. Isolated 1010 now rejects; 1001 and 1014 still pay in real xAI CLI runs. See [catalog verification evidence](build/status.md#catalog-price-validation-follow-up) for commands, results, artifacts and limitations.

Promoted to [ADR-008](decisions/README.md#adr-008-catalog-price-validation-with-a-tolerance-band). Implementation touches `invoice_agent/sql/schema.sql`, `invoice_agent/sql/seed.sql`, `invoice_agent/tools.py`, `invoice_agent/validation.py`, and `tests/fixtures/expected/outcomes.json`.

### 2. HTML result artifact — accepted

Terminal progress is readable, but the durable output is JSONL plus an ASCII table. A single `runs/<run_id>/report.html` rendered from the existing `InvoiceResult` models covers the assignment's UI/UX criterion for a non-technical reviewer. No template dependency; a formatted string is enough. Findings, reasons, and the summary table are already structured data.

### 3. README business-impact framing — accepted

"Clear translation of technical decisions to business impact" is a named scoring criterion. The README currently never mentions the $2M annual loss, the 30% error rate, or the 5-day delay from the brief. The metrics section already reports blocked-payment exposure with the correct caveat that it is not realized savings; that instinct needs to be stated up front and each major design decision mapped to the pain point it addresses.

## Declined, with reasoning

Recording these because the reasoning matters more than the outcome.

**Removing batch and chronological ordering.** The review's strongest criticism was that folder processing solves no stated problem — the brief's only usage example passes a single file, and the sample invoices are independent of one another. That reading is correct, and folder processing is why a full run takes minutes rather than seconds. It is nonetheless already built, tested, and documented in [ADR-001](decisions/README.md#adr-001-fresh-invocations-stateful-chronological-folders). Removing it now costs more risk than it returns. The response is presentational: ADR-001 and the README should state plainly that the primary path is one invoice against fresh state, and that folder processing is a scoped extension.

**A raw SQL tool for the agent.** Suggested as essential. Declined: the agent's context is populated from untrusted invoice text, and arbitrary SQL against the inventory and payment ledger is an injection surface for no capability gain. The typed `lookup_inventory` tool with independent coverage verification in `tools.py` already proves real function calling. Worth one line in the decision log, since a reviewer may reach for the same idea.

**Progressive discount tiers.** Suggested as a richer pricing rule. No invoice in the corpus uses tiered pricing, so the rule would be untestable against the supplied data and would be speculative complexity. A single catalog price with a tolerance band covers every observed case.

## Price evidence

Every unit price in `data/invoices/`, including the three PDFs, extracted and cross-checked. This is the basis for the seed values in ADR-008.

| Item | Price | Currency | Sources |
|---|---|---|---|
| WidgetA | **250.00** | USD | 1001, 1004, 1004r, 1005, 1006, 1007, 1009, 1010, 1011 (txt+pdf), 1012 (txt+pdf), 1013 ×2 (json+pdf), 1015, 1016 |
| WidgetA | 240.00 | USD | 1013, line noted "Volume discount" |
| WidgetA | 300.00 | USD | 1010, line noted "(rush order)" |
| WidgetA | 225.00 | EUR | 1014 |
| WidgetB | **500.00** | USD | 1001, 1004, 1004r, 1005, 1006, 1007, 1009, 1010, 1011 (txt+pdf), 1012 (txt+pdf), 1013 (json+pdf), 1015, 1016 |
| WidgetB | 480.00 | USD | 1013, line noted "Volume discount" |
| WidgetB | 475.00 | EUR | 1014 |
| GadgetX | **750.00** | USD | 1002, 1004r, 1005, 1007, 1010, 1012 (txt+pdf), 1013 ×3 (json+pdf), 1015 — no exceptions |
| FakeItem | 1000.00 | USD | 1003 only; zero-stock item from "Fraudster LLC" |
| SuperGizmo / MegaSprocket | 400.00 / 850.00 | USD | 1008; not in inventory |
| WidgetC | 350.00 | USD | 1016; not in inventory |

Base prices are consistent at 250 / 500 / 750 USD, with three deviations. One of them constrains the design:

`invoice_1014.xml` is denominated in EUR, and at the fixed policy rate of 1.10 in `config.py` it does not convert to catalog price.

- WidgetA: 225.00 EUR × 1.10 = $247.50 against $250 → −1.0%
- WidgetB: 475.00 EUR × 1.10 = $522.50 against $500 → **+4.5%**

Invoice 1014 is expected to be approved. An exact-match price check would reject it — a false positive on a legitimate invoice. The check therefore needs a tolerance band wide enough to absorb FX and rounding at +4.5% but tight enough to catch the +20% rush-order overcharge. Nothing in the corpus falls between those two figures, so 10% separates them cleanly.

Consequence for discount handling: the 1013 volume discounts are −4% on both lines and fall inside a symmetric 10% band, so a price-delta rule never fires on them. If the discount-review path is worth demonstrating, trigger it from the line-item note text ("Volume discount", "Expedited", "Rush") rather than from the price delta — that is the signal the data actually carries. Invoice 1013 rejects on stock regardless, so this changes no outcome.
