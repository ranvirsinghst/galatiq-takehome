"""Real catalog, rule, tool, graph, and transactional payment regressions."""

from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from invoice_agent.config import Policy
from invoice_agent.database import SQLitePaymentStore
from invoice_agent.errors import AgentError
from invoice_agent.graph import process_invoice
from invoice_agent.models import CatalogEvidence, LLMResponse, LLMToolCall
from invoice_agent.money import convert_to_usd
from invoice_agent.output import EventCollector
from invoice_agent.tools import validate_with_tools
from invoice_agent.validation import validate
from tests.doubles import ScenarioLLM
from tests.fixture_model import FixtureLLM
from tests.integration.test_batch import execute
from tests.integration.test_inventory_tools import Scripted
from tests.integration.test_storage_payment import request, success
from tests.unit.test_validation import candidate


def priced(price="250", currency="USD", item="WidgetA", note="Rush order"):
    amount = Decimal(price)
    rate = Policy().fx_rates[currency]
    line = (
        candidate()
        .items[0]
        .model_copy(
            update={
                "source_unit_price": amount,
                "unit_price_usd": convert_to_usd(amount, rate),
                "source_line_total": amount * 2,
                "item_name_normalized": item,
                "description_raw": item,
                "source_tokens": {"notes": note},
                "evidence_refs": ["source-line"],
            }
        )
    )
    return candidate(
        items=[line],
        source_currency=currency,
        fx_rate_to_usd=rate,
        source_amounts={"subtotal": amount * 2, "total": amount * 2},
        total_usd=convert_to_usd(amount * 2, rate),
    )


@pytest.fixture
def store(tmp_path):
    db = SQLitePaymentStore(tmp_path / "catalog.db", "run")
    yield db
    db.close()


def checked(store, c):
    items = [line.item_name_normalized for line in c.items]
    return validate(c, store.lookup(items), store.policy, store.lookup_price(items))


@pytest.mark.parametrize(
    "price,currency,item,code,severity",
    [
        ("300", "USD", "WidgetA", "PRICE_OVERCHARGE", "blocker"),
        ("275", "USD", "WidgetA", None, None),
        ("225", "USD", "WidgetA", None, None),
        ("274.99", "USD", "WidgetA", None, None),
        ("225.01", "USD", "WidgetA", None, None),
        ("275.01", "USD", "WidgetA", "PRICE_OVERCHARGE", "blocker"),
        ("224.99", "USD", "WidgetA", "PRICE_UNDER_CATALOG", "warning"),
        ("240", "USD", "WidgetA", None, None),
        ("225", "EUR", "WidgetA", None, None),
        ("475", "EUR", "WidgetB", None, None),
        ("225.004", "EUR", "WidgetA", None, None),
    ],
)
def test_price_boundaries_and_fixed_fx(store, price, currency, item, code, severity):
    c = priced(price, currency, item)
    report = checked(store, c)
    assert report.complete
    findings = [f for f in report.findings if f.code.startswith("PRICE_")]
    assert [f.code for f in findings] == ([code] if code else [])
    if findings:
        f = findings[0]
        assert f.severity == severity
        assert f.line_ids == ["1"] and f.pricing_item == item
        assert Decimal(f.observed) == c.items[0].unit_price_usd
        assert Decimal(f.expected) == 250
        assert f.deviation_ratio == (Decimal(f.observed) - 250) / 250
        assert f.tolerance_ratio == Decimal(".10")
        assert "Rush order" in f.message and f.evidence_refs == ["source-line"]


@pytest.mark.parametrize("value", ["-0.01", "1", "NaN", "Infinity"])
def test_invalid_tolerance(value):
    with pytest.raises(ValidationError):
        Policy(price_tolerance_ratio=Decimal(value))


def test_catalog_seed_null_and_parameterization(store, tmp_path):
    expected = {"WidgetA": Decimal("250"), "WidgetB": Decimal("500"), "GadgetX": Decimal("750")}
    assert store.lookup_price(list(expected)).prices == expected
    assert store.lookup_price(["FakeItem", "unknown", "'; DROP TABLE prices; --"]).prices == {
        "FakeItem": None,
        "unknown": None,
        "'; DROP TABLE prices; --": None,
    }
    assert store.connection.execute("SELECT count(*) FROM prices").fetchone()[0] == 3
    assert "generation" not in store.lookup_price(["WidgetA"]).model_dump()
    second = SQLitePaymentStore(tmp_path / "second.db", "other")
    assert second.lookup_price(list(expected)).prices == expected
    second.close()


@pytest.mark.parametrize("value", ["0", "-1", "NaN", "Infinity", "broken"])
def test_malformed_catalog_is_structured_error(store, value):
    store.connection.execute("UPDATE prices SET unit_price_usd=? WHERE item='WidgetA'", (value,))
    with pytest.raises(AgentError) as error:
        store.lookup_price(["WidgetA"])
    assert error.value.code == "STORAGE_ERROR"
    result = process_invoice(
        priced(),
        "x",
        0,
        store,
        ScenarioLLM(),
        store.policy,
        EventCollector("run"),
        lambda *_: pytest.fail("invalid catalog reached mock"),
    )
    assert result.status == "error" and result.error.code == "STORAGE_ERROR"
    assert store.snapshot().stock["WidgetA"] == 15


def test_absent_prices_incomplete_and_missing_row_operational(store):
    c = priced()
    r = validate(c, store.lookup(["WidgetA"]), store.policy)
    assert not r.complete and "price:1" in r.unavailable_checks
    store.connection.execute("DELETE FROM prices WHERE item='WidgetA'")
    with pytest.raises(AgentError):
        checked(store, c)
    store.connection.execute("UPDATE inventory SET stock=0 WHERE item='WidgetA'")
    assert checked(store, c).blockers[0].code == "OUT_OF_STOCK"


def test_missing_invoice_price_and_usd_tampering(store):
    c = priced()
    missing = c.model_copy(
        update={"items": [c.items[0].model_copy(update={"source_unit_price": None})]}
    )
    assert "PRICE_CHECK_UNAVAILABLE" in {f.code for f in checked(store, missing).blockers}
    altered = c.model_copy(
        update={"items": [c.items[0].model_copy(update={"unit_price_usd": Decimal(1)})]}
    )
    assert "USD_CONVERSION_MISMATCH" in {f.code for f in checked(store, altered).blockers}


def test_repeated_lines_do_not_offset_and_shipping_not_priced(store):
    a, b = priced("300").items[0], priced("200").items[0].model_copy(update={"line_id": "2"})
    c = priced().model_copy(
        update={
            "items": [a, b],
            "total_usd": Decimal(1100),
            "source_amounts": {
                "subtotal": Decimal(1000),
                "shipping": Decimal(100),
                "total": Decimal(1100),
            },
        }
    )
    r = checked(store, c)
    assert r.aggregate_quantities == {"WidgetA": 4}
    assert {f.code for f in r.findings} == {"PRICE_OVERCHARGE", "PRICE_UNDER_CATALOG"}


def call(name, items=None, id="call"):
    return LLMToolCall(call_id=id, name=name, arguments={"items": items or ["WidgetA"]})


@pytest.mark.parametrize(
    "rounds",
    [
        [[call("lookup_price", id="p"), call("lookup_inventory")]],
        [[call("lookup_inventory")], [call("lookup_price")]],
        [[call("lookup_price")], [call("lookup_inventory")]],
    ],
)
def test_separate_tool_coverage_and_order(store, rounds):
    events = EventCollector("run")
    result = validate_with_tools(
        priced(),
        store,
        Scripted([LLMResponse(tool_calls=calls) for calls in rounds]),
        store.policy,
        events,
    )
    assert result.report.complete
    assert result.report.catalog_evidence.prices == {"WidgetA": Decimal(250)}
    results = [e for e in events.events if e.event == "tool_result"]
    assert {e.payload["name"] for e in results} == {"lookup_inventory", "lookup_price"}


@pytest.mark.parametrize(
    "bad",
    [
        call("lookup_price", id="call"),
        call("lookup_price", id=" "),
        LLMToolCall(
            call_id="bad", name="lookup_price", arguments={"items": ["WidgetA"], "sql": "bad"}
        ),
        call("lookup_price", ["widgeta"], "bad"),
    ],
)
def test_response_preflight_prevents_all_reads(store, bad, monkeypatch):
    from unittest.mock import Mock

    inventory_read = Mock(wraps=store.lookup)
    price_read = Mock(wraps=store.lookup_price)
    monkeypatch.setattr(store, "lookup", inventory_read)
    monkeypatch.setattr(store, "lookup_price", price_read)
    events = EventCollector("run")
    response = LLMResponse(tool_calls=[call("lookup_inventory"), bad])
    result = validate_with_tools(priced(), store, Scripted([response] * 2), store.policy, events)
    assert result.error.code == "TOOL_PROTOCOL_ERROR" and not events.events
    inventory_read.assert_not_called()
    price_read.assert_not_called()


def test_inventory_only_never_completes_eligible_price_checks(store):
    response = LLMResponse(tool_calls=[call("lookup_inventory")])
    result = validate_with_tools(
        priced(), store, Scripted([response] * 2), store.policy, EventCollector("run")
    )
    assert result.error.code == "TOOL_PROTOCOL_ERROR"
    blocked = priced().model_copy(update={"total_usd": Decimal(-1)})
    result = validate_with_tools(
        blocked, store, Scripted([response]), store.policy, EventCollector("run")
    )
    assert result.report.blockers and result.error is None


@pytest.mark.parametrize("kind", ["omitted", "wrong_run"])
def test_invalid_catalog_tool_binding(store, kind):
    class Reader:
        run_id = store.run_id
        lookup = store.lookup

        def lookup_price(self, items):
            return CatalogEvidence(
                run_id="wrong" if kind == "wrong_run" else "run",
                prices={} if kind == "omitted" else {"WidgetA": Decimal(250)},
            )

    response = LLMResponse(tool_calls=[call("lookup_price", id="p"), call("lookup_inventory")])
    result = validate_with_tools(
        priced(), Reader(), Scripted([response] * 2), store.policy, EventCollector("run")
    )
    assert result.error.code == "TOOL_PROTOCOL_ERROR"


@pytest.mark.parametrize(
    "tamper", ["absent", "fabricated", "run", "changed_row", "findings", "candidate"]
)
def test_direct_payment_catalog_bypasses_fail_before_mock(store, tamper):
    req = request(store, priced("200"))
    report = req.report
    if tamper == "absent":
        report = report.model_copy(update={"catalog_evidence": None})
    elif tamper in ("fabricated", "run"):
        evidence = CatalogEvidence(
            run_id="other" if tamper == "run" else "run",
            prices={"WidgetA": Decimal(200) if tamper == "fabricated" else Decimal(250)},
        )
        report = report.model_copy(update={"catalog_evidence": evidence})
    elif tamper == "changed_row":
        store.connection.execute("UPDATE prices SET unit_price_usd='200' WHERE item='WidgetA'")
    elif tamper == "findings":
        report = report.model_copy(update={"findings": []})
    else:
        req = req.model_copy(update={"candidate": priced("300")})
    result = store.pay(
        req.model_copy(update={"report": report}), lambda *_: pytest.fail("bypass called mock")
    )
    assert result.status == "not_paid" and result.findings
    assert store.snapshot().stock["WidgetA"] == 15
    assert store.connection.execute("SELECT count(*) FROM payments").fetchone()[0] == 0


def test_underprice_warning_requires_acknowledgment_and_can_pay(store):
    llm = ScenarioLLM()
    result = process_invoice(
        priced("200"), "x", 0, store, llm, store.policy, EventCollector("run"), success
    )
    assert result.payment.status == "paid"
    assert "PRICE_UNDER_CATALOG" in result.review.proposal.finding_codes
    assert [r.phase for r in llm.requests] == ["inventory", "vp_propose", "vp_critique"]


@pytest.mark.parametrize(
    "invoice,paid,amount,stock",
    [
        ("1010.txt", False, "7185.00", {"WidgetA": 15, "WidgetB": 10}),
        ("1001.txt", True, "5000.00", {"WidgetA": 5, "WidgetB": 5}),
        ("1014.xml", True, "4537.50", {"WidgetA": 11, "WidgetB": 4}),
    ],
)
def test_real_isolated_corpus_catalog_evidence(tmp_path, invoice, paid, amount, stock):
    llm = FixtureLLM()
    result = execute([Path("data/invoices/invoice_" + invoice)], tmp_path, llm=llm)
    item = result.results[0]
    assert item.total_usd == Decimal(amount)
    assert (item.payment.status == "paid") == paid
    assert item.validation.catalog_evidence is not None
    for name, quantity in stock.items():
        assert result.summary.final_inventory[name] == quantity
    if not paid:
        assert item.review is None and not any(r.phase.startswith("vp") for r in llm.requests)
        finding = next(f for f in item.findings if f.code == "PRICE_OVERCHARGE")
        assert "rush order" in finding.message.lower() and finding.deviation_ratio == Decimal(".2")


def test_already_blocked_retains_business_rejection_with_invalid_optional_catalog(store):
    store.connection.execute("UPDATE prices SET unit_price_usd='broken' WHERE item='WidgetA'")
    c = priced().model_copy(update={"total_usd": Decimal(-1)})
    for calls in (
        [call("lookup_price", id="p"), call("lookup_inventory")],
        [call("lookup_inventory"), call("lookup_price", id="p")],
    ):
        result = validate_with_tools(
            c, store, Scripted([LLMResponse(tool_calls=calls)]), store.policy, EventCollector("run")
        )
        assert result.error is None and result.report.blockers


def test_catalog_changes_between_precheck_and_transaction_fail_closed(store):
    req = request(store, priced())
    actual = store.lookup

    def changing_lookup(items):
        store.connection.execute("UPDATE prices SET unit_price_usd='251' WHERE item='WidgetA'")
        return actual(items)

    store.lookup = changing_lookup
    result = store.pay(req, lambda *_: pytest.fail("changed transactional catalog reached mock"))
    assert result.findings[0].code == "INVALID_PAYMENT_REQUEST"
    assert store.snapshot().stock["WidgetA"] == 15
    assert store.connection.execute("SELECT count(*) FROM payments").fetchone()[0] == 0
    assert store.lookup_price(["WidgetA"]).prices["WidgetA"] == Decimal(250)


def test_rejected_overcharge_preserves_stock_for_later_invoice(tmp_path):
    import json

    from tests.integration.test_batch import invoice

    early = invoice(tmp_path / "early.json", "EARLY", "2026-01-01", 15)
    later = invoice(tmp_path / "later.json", "LATER", "2026-02-01", 15)
    for path, price in ((early, 300), (later, 250)):
        data = json.loads(path.read_text())
        data["line_items"][0]["unit_price"] = price
        data["subtotal"] = data["total"] = price * 15
        path.write_text(json.dumps(data))
    result = execute([later, early], tmp_path)
    assert [r.decision for r in result.results] == ["rejected", "approved"]
    assert result.summary.final_inventory["WidgetA"] == 0
    assert result.summary.total_paid_usd == Decimal(3750)


def test_catalog_console_and_historical_report(tmp_path):
    from io import StringIO

    from invoice_agent.console import ConsoleReporter
    from invoice_agent.models import RunResult
    from invoice_agent.report import render_report

    result = execute([Path("data/invoices/invoice_1010.txt")], tmp_path, llm=FixtureLLM())
    html = render_report(result)
    assert "Catalog price lookup result" in html
    assert "Catalog price evidence used for review" in html
    assert "PRICE_OVERCHARGE" in html and "rush order" in html
    stream = StringIO()
    reporter = ConsoleReporter([Path("invoice_1010.txt")], stream, trace=True)
    for event in result.trace:
        reporter.event(event)
    reporter.invoice(result.results[0], stream)
    assert "Catalog price lookup (USD/unit)" in stream.getvalue()
    assert "VP review skipped" in stream.getvalue()
    old = result.model_dump(mode="json")
    old["results"][0]["validation"].pop("catalog_evidence")
    old["trace"] = []
    old["results"][0]["trace"] = []
    old_html = render_report(RunResult.model_validate(old))
    assert "Catalog price evidence used for review" not in old_html


@pytest.mark.parametrize("price_first", [True, False])
@pytest.mark.parametrize("blocker", ["source", "stock", None])
def test_deferred_catalog_error_survives_split_collection(store, price_first, blocker):
    store.connection.execute("UPDATE prices SET unit_price_usd='broken' WHERE item='WidgetA'")
    c = priced()
    if blocker == "source":
        c = c.model_copy(update={"total_usd": Decimal(-1)})
    if blocker == "stock":
        store.connection.execute("UPDATE inventory SET stock=0 WHERE item='WidgetA'")
    names = (
        ["lookup_price", "lookup_inventory"]
        if price_first
        else ["lookup_inventory", "lookup_price"]
    )
    llm = Scripted([LLMResponse(tool_calls=[call(name)]) for name in names])
    outcome = validate_with_tools(c, store, llm, store.policy, EventCollector("run"))
    if blocker:
        assert outcome.error is None and outcome.report.blockers
        assert outcome.tool_rounds == (2 if price_first else 1)
    else:
        assert outcome.error.code == "STORAGE_ERROR" and outcome.tool_rounds == 2


@pytest.mark.parametrize("bad_kind", ["omitted", "wrong_run"])
def test_recovery_adapter_history_and_serialized_actual_prices(store, bad_kind):
    import json

    import httpx

    from invoice_agent.llm import XAIClient

    actual = store.lookup_price
    reads = []

    def price_read(items):
        reads.append(items)
        if len(reads) == 1:
            return CatalogEvidence(
                run_id="other" if bad_kind == "wrong_run" else "run",
                prices={} if bad_kind == "omitted" else {"WidgetA": Decimal(250)},
            )
        return actual(items)

    store.lookup_price = price_read
    payloads = []

    def endpoint(request):
        payload = json.loads(request.content)
        payloads.append(payload)
        pending = set()
        for message in payload["messages"]:
            if message["role"] == "assistant":
                assert not pending
                pending = {c["id"] for c in message.get("tool_calls", [])}
            elif message["role"] == "tool":
                assert message["tool_call_id"] in pending
                pending.remove(message["tool_call_id"])
            else:
                assert not pending
        assert not pending
        calls = [call("lookup_inventory", id="i"), call("lookup_price", id="p")]
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "tool_calls": [
                                {
                                    "id": c.call_id,
                                    "type": "function",
                                    "function": {
                                        "name": c.name,
                                        "arguments": json.dumps(c.arguments),
                                    },
                                }
                                for c in calls
                            ]
                        },
                    }
                ]
            },
        )

    events = EventCollector("run")
    llm = XAIClient("test-key", transport=httpx.MockTransport(endpoint))
    try:
        outcome = validate_with_tools(priced(), store, llm, store.policy, events)
    finally:
        llm.close()
    assert outcome.report.complete and len(payloads) == 2
    assert outcome.report.catalog_evidence.model_dump(mode="json")["prices"] == {
        "WidgetA": "250.00"
    }
    results = [
        e.payload
        for e in events.events
        if e.event == "tool_result" and e.payload["name"] == "lookup_price"
    ]
    assert results[-1]["prices"] == {"WidgetA": "250.00"}


@pytest.mark.parametrize("failure", ["acknowledgment", "critique"])
def test_discount_review_refusal_is_bounded(store, failure):
    class Refusing(ScenarioLLM):
        def complete(self, request):
            response = super().complete(request)
            if failure == "acknowledgment" and request.phase in ("vp_propose", "vp_revise"):
                return response.model_copy(
                    update={"content": {**response.content, "finding_codes": []}}
                )
            if failure == "critique" and request.phase == "vp_critique":
                return LLMResponse(
                    content={
                        "verdict": "revise",
                        "reason_summary": "The proposed discount needs further explanation before this reviewer can accept it.",
                        "issues": ["Explain discount"],
                        "required_changes": ["Explain discount"],
                    }
                )
            return response

    llm = Refusing()
    outcome = process_invoice(
        priced("200"),
        "x",
        0,
        store,
        llm,
        store.policy,
        EventCollector("run"),
        lambda *_: pytest.fail("unacknowledged discount reached mock"),
    )
    assert outcome.payment.status != "paid" and not outcome.review.accepted
    assert len([r for r in llm.requests if r.phase.startswith("vp_")]) == 6
    assert store.snapshot().stock["WidgetA"] == 15


def test_high_value_overcharge_skips_even_approving_provider(store):
    c = priced("300").model_copy(
        update={
            "total_usd": Decimal(12000),
            "source_amounts": {
                "subtotal": Decimal(600),
                "shipping": Decimal(11400),
                "total": Decimal(12000),
            },
        }
    )
    llm = ScenarioLLM(hostile=True)
    result = process_invoice(
        c,
        "x",
        0,
        store,
        llm,
        store.policy,
        EventCollector("run"),
        lambda *_: pytest.fail("overcharge reached mock"),
    )
    assert result.validation.requires_high_value_review and result.review is None
    assert [r.phase for r in llm.requests] == ["inventory"]
    assert result.decision == "rejected"


@pytest.mark.parametrize("omit_second_price", [True, False])
def test_multiple_item_incremental_independent_coverage(store, omit_second_price):
    c = priced().model_copy(
        update={
            "items": [
                priced().items[0],
                priced("500", item="WidgetB").items[0].model_copy(update={"line_id": "2"}),
            ],
            "total_usd": Decimal(1500),
            "source_amounts": {"subtotal": Decimal(1500), "total": Decimal(1500)},
        }
    )
    rounds = [
        [call("lookup_inventory", ["WidgetA"], "ia"), call("lookup_price", ["WidgetB"], "pb")],
        [call("lookup_inventory", ["WidgetB"], "ib")],
    ]
    if not omit_second_price:
        rounds[1].append(call("lookup_price", ["WidgetA"], "pa"))
    llm = Scripted([LLMResponse(tool_calls=calls) for calls in rounds])
    result = validate_with_tools(c, store, llm, store.policy, EventCollector("run"))
    if omit_second_price:
        assert result.error.code == "TOOL_PROTOCOL_ERROR"
    else:
        assert result.report.complete
        assert result.report.catalog_evidence.prices == {
            "WidgetA": Decimal(250),
            "WidgetB": Decimal(500),
        }
    import json

    replies = [json.loads(m["content"]) for m in llm.requests[1].messages if m["role"] == "tool"]
    assert {"run_id": "run", "prices": {"WidgetB": "500.00"}} in replies


@pytest.mark.parametrize("tolerance,price", [("0", "250"), ("0.20", "300")])
def test_nondefault_tolerance_reaches_final_gate(store, tolerance, price):
    store.policy = Policy(price_tolerance_ratio=Decimal(tolerance))
    c = priced(price)
    req = request(store, c)
    assert store.pay(req, success).status == "paid"
    assert store.snapshot().stock["WidgetA"] == 13


@pytest.mark.parametrize("fault", ["malformed", "unavailable"])
def test_catalog_transaction_operational_fault_rolls_back_and_is_persisted(store, tmp_path, fault):
    import json

    from invoice_agent.output import write_invoice
    from invoice_agent.runner import RunDependencies, run
    from tests.integration.test_batch import invoice

    actual = store.lookup

    def failing_lookup(items):
        if store.connection.in_transaction:
            if fault == "malformed":
                store.connection.execute(
                    "UPDATE prices SET unit_price_usd='broken' WHERE item='WidgetA'"
                )
            else:
                store.connection.execute("DROP TABLE prices")
        return actual(items)

    store.lookup = failing_lookup
    events = EventCollector("run")
    source = invoice(tmp_path / "invoice.json", "CATALOG", "2026-01-01", 2)
    data = json.loads(source.read_text())
    data["line_items"][0]["unit_price"] = 250
    data["subtotal"] = data["total"] = 500
    source.write_text(json.dumps(data))
    with (tmp_path / "results.jsonl").open("w") as stream:
        batch = run(
            [source],
            RunDependencies(
                store,
                ScenarioLLM(),
                events,
                store.policy,
                mock=lambda *_: pytest.fail("catalog fault reached mock"),
                on_result=lambda item: write_invoice(item, stream),
            ),
        )
    result = batch.results[0]
    assert result.status == "error" and result.error.code == "STORAGE_ERROR"
    assert not store.connection.in_transaction
    assert store.snapshot().stock["WidgetA"] == 15
    assert store.connection.execute("SELECT count(*) FROM payments").fetchone()[0] == 0
    assert store.lookup_price(["WidgetA"]).prices["WidgetA"] == Decimal(250)
    saved = json.loads((tmp_path / "results.jsonl").read_text())
    assert saved["error"]["code"] == "STORAGE_ERROR"
    assert any(
        e.event == "tool_result" and e.payload.get("prices") == {"WidgetA": "250.00"}
        for e in events.events
    )


def test_adapter_sends_decimal_string_price_reply_before_inventory_round(store):
    import json

    import httpx

    from invoice_agent.llm import XAIClient

    payloads = []

    def endpoint(request):
        payload = json.loads(request.content)
        payloads.append(payload)
        if len(payloads) == 2:
            replies = [m for m in payload["messages"] if m["role"] == "tool"]
            assert len(replies) == 1 and replies[0]["tool_call_id"] == "price"
            assert replies[0]["name"] == "lookup_price"
            assistant = next(m for m in payload["messages"] if m["role"] == "assistant")
            assert assistant["tool_calls"][0]["id"] == replies[0]["tool_call_id"]
            assert assistant["tool_calls"][0]["function"]["name"] == replies[0]["name"]
            assert json.loads(replies[0]["content"]) == {
                "run_id": "run",
                "prices": {"WidgetA": "250.00"},
            }
        name, identifier = (
            ("lookup_price", "price") if len(payloads) == 1 else ("lookup_inventory", "inventory")
        )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "tool_calls": [
                                {
                                    "id": identifier,
                                    "type": "function",
                                    "function": {
                                        "name": name,
                                        "arguments": '{"items":["WidgetA"]}',
                                    },
                                }
                            ]
                        },
                    }
                ]
            },
        )

    llm = XAIClient("test-key", transport=httpx.MockTransport(endpoint))
    try:
        result = validate_with_tools(priced(), store, llm, store.policy, EventCollector("run"))
    finally:
        llm.close()
    assert result.report.complete and len(payloads) == 2


def test_non_typed_catalog_tool_result_fails_within_budget(store, monkeypatch):
    actual = store.lookup_price
    reads = []

    def untyped_price(items):
        evidence = actual(items)
        reads.append(evidence)
        return evidence.model_dump(mode="json")

    monkeypatch.setattr(store, "lookup_price", untyped_price)
    response = LLMResponse(tool_calls=[call("lookup_price", id="p"), call("lookup_inventory")])
    result = validate_with_tools(
        priced(),
        store,
        Scripted([response] * store.policy.tool_rounds),
        store.policy,
        EventCollector("run"),
    )
    assert len(reads) == result.tool_rounds == store.policy.tool_rounds
    assert result.error.code == "TOOL_PROTOCOL_ERROR" and result.report is None
    assert all(e.prices == {"WidgetA": Decimal(250)} for e in reads)


def test_catalog_value_changes_between_incremental_rounds_fail_closed(store):
    class ChangingCatalog(Scripted):
        def complete(self, request):
            if self.requests:
                store.connection.execute(
                    "UPDATE prices SET unit_price_usd='251.00' WHERE item='WidgetA'"
                )
            return super().complete(request)

    llm = ChangingCatalog(
        [
            LLMResponse(tool_calls=[call("lookup_price", id="first-price")]),
            LLMResponse(
                tool_calls=[
                    call("lookup_inventory", id="stock"),
                    call("lookup_price", id="second-price"),
                ]
            ),
        ]
    )
    events = EventCollector("run")
    result = validate_with_tools(priced(), store, llm, store.policy, events)
    assert result.tool_rounds == store.policy.tool_rounds == 2
    assert result.error.code == "TOOL_PROTOCOL_ERROR" and result.report is None
    assert store.lookup_price(["WidgetA"]).prices == {"WidgetA": Decimal("251.00")}
    first_result = next(e for e in events.events if e.event == "tool_result")
    assert first_result.payload["prices"] == {"WidgetA": "250.00"}
    assert store.snapshot().stock["WidgetA"] == 15
    assert store.connection.execute("SELECT count(*) FROM payments").fetchone()[0] == 0


def test_direct_validation_rejects_catalog_from_another_run(store):
    evidence = store.lookup_price(["WidgetA"]).model_copy(update={"run_id": "another-run"})
    with pytest.raises(ValueError, match="Catalog evidence run identity mismatch"):
        validate(priced(), store.lookup(["WidgetA"]), store.policy, evidence)
