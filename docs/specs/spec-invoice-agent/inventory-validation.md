# D — Inventory tool and deterministic validation

## Boundary

Own `tools.py`, `validation.py`, and associated unit/integration tests. Consume A contracts, C runtime, and E read-only inventory port. Validation code cannot mutate stock, insert ledger records, or invoke payment. It sees the current snapshot for one processing position, not a cached seed snapshot.

Export `validate_with_tools` as the actual bounded tool graph and `validate` as its pure policy function, using the shared signatures. Consumers must not recreate the tool loop in the coordinator.

Expose `lookup_inventory(items: list[str])` to the model. Validate tool name/arguments; return each requested normalized item with stock or an explicit missing marker. Use parameterized SQLite queries through E. A supplied item string is not SQL or a filename.

## Agent and deterministic split

The validation graph asks Grok to request inventory facts. Record the actual request and tool result. Allow at most two tool-request rounds. Verify coverage independently against all normalized candidate items; a model omitting an item must not allow it to bypass lookup. Recover missing coverage in the next bounded round or emit a tool protocol error. Never fabricate a tool call event for a direct Python query.

Once evidence is complete, call the pure `validate(candidate, snapshot, policy)` function. It returns findings, aggregates, checked/unavailable arithmetic, invoice digest, and snapshot generation. It must not trust a model statement that stock or quantities are valid.

Keep source/structural findings even if stock lookup cannot proceed for an invalid candidate. Ingestion-terminal rejections need not make a pointless inventory call. Every eligible processing path demonstrates actual tool use; incomplete evidence blocks payment.

## Rules

| Rule | Finding / outcome |
|---|---|
| Missing required vendor/identity/date/due/items/total | Blocker |
| Quantity zero, negative, fractional, boolean, nonnumeric, non-finite | `INVALID_QUANTITY`, blocker |
| Unknown normalized item | `UNKNOWN_ITEM`, blocker |
| Known stock zero | `OUT_OF_STOCK`, blocker |
| Aggregated valid demand greater than current stock | `INSUFFICIENT_STOCK`, blocker |
| Total <= 0 or invalid finite-money representation | `INVALID_AMOUNT`, blocker |
| Supplied unit price, line amount, subtotal, tax or shipping is malformed, negative or non-finite | `INVALID_AMOUNT`, blocker |
| Source arithmetic discrepancy > 0.01 source currency | `TOTAL_MISMATCH`, blocker |
| Unsupported source currency | `UNSUPPORTED_CURRENCY`, blocker |
| Due date earlier than issue date | `DUE_BEFORE_ISSUE`, blocker |
| Explicit due date differs from issue date + supported Net N | `TERMS_DATE_MISMATCH`, warning |
| Missing currency interpreted as USD | Recorded assumption/warning |
| Today is later than fixture due date | No standalone rejection |

Only aggregate positive integral quantities, while retaining blockers for invalid lines; never let a negative line cancel valid demand. Repeated item lines with distinct prices still consume combined stock. Unknown items are never created automatically.

Arithmetic checks use source amounts, Decimal and the declared one-cent absolute tolerance. Verify available quantity × price, line sum versus subtotal, and subtotal + explicit charges versus payable total. Mark genuinely unavailable checks instead of inventing optional amounts. USD conversion uses the source payable total directly, not a sum of rounded USD lines.

The >10000 threshold is not a validator rejection rule. Return an explicit high-value requirement for F, computed from canonical USD payable total.

## Unit tests

- Fresh seed clean fixtures pass stock validation; each blocker has a focused minimal case.
- GadgetX 20 against 5 fails, while Net 30 creates a separate terms warning for 1002.
- Repeated demand 15+5+2 for WidgetA is 22, not 15, and fails against 15.
- Negative quantity does not offset demand; Python True does not equal one valid unit.
- Unknown WidgetC remains unknown; explicit aliases work without fuzzy substitutions.
- Compare exactly stock, one over, zero stock, and absent item separately.
- Source totals differing by .01 and .02 exercise the chosen tolerance boundary.
- 1013 JSON: subtotal 21040.00 + tax 1472.80 = 22512.80 versus stated 22562.80, discrepancy 50.00.
- USD threshold values 10000.00 and 10000.01; EUR conversion crossing the threshold.
- Net 30 date arithmetic crosses month/year/leap boundaries; no implicit current date.
- Missing optional pricing reports arithmetic unavailable rather than fabricated success; supplied invalid, negative, or non-finite pricing/charges emits `INVALID_AMOUNT`.

## Integration tests

Run the actual tool node and validator with real SQLite and a scripted LLM:

- Complete tool request passes evidence to deterministic rules.
- Missing item on first call is recovered on second; missing after second fails without payment.
- Unknown tool name, malformed arguments, fabricated stock in prose, or mismatched tool ID does not supply trusted evidence.
- Stock decremented by an earlier payment appears in the next lookup.
- A malicious item name is treated as a parameter; inventory table survives unchanged.

Assert raw tool evidence, findings, and no stock/ledger mutation during validation.
