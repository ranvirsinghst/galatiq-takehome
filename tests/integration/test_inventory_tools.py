import pytest

from invoice_agent.database import SQLitePaymentStore
from invoice_agent.models import LLMResponse, LLMToolCall
from invoice_agent.tools import validate_with_tools
from tests.integration.test_storage_payment import Events
from tests.unit.test_validation import candidate


class Scripted:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        return next(self.responses)


def response(items, name="lookup_inventory", call_id="call-1"):
    return LLMResponse(
        tool_calls=[LLMToolCall(call_id=call_id, name=name, arguments={"items": items})]
    )


def test_real_graph_calls_real_inventory_without_mutation(tmp_path):
    store = SQLitePaymentStore(tmp_path / "db", "run")
    events = Events()
    llm = Scripted([response(["WidgetA"])])
    result = validate_with_tools(candidate(), store, llm, store.policy, events)
    assert result.report.complete and not result.report.blockers
    assert result.tool_rounds == 1
    assert [e.event for e in events.events] == ["tool_requested", "tool_result"]
    assert store.snapshot().stock["WidgetA"] == 15
    assert store.connection.execute("SELECT count(*) FROM payments").fetchone()[0] == 0
    store.close()


def test_missing_item_recovered_second_round(tmp_path):
    store = SQLitePaymentStore(tmp_path / "db", "run")
    c = candidate()
    c = c.model_copy(
        update={
            "items": [
                c.items[0],
                c.items[0].model_copy(update={"line_id": "2", "item_name_normalized": "WidgetB"}),
            ]
        }
    )
    llm = Scripted([response(["WidgetA"]), response(["WidgetB"], call_id="call-2")])
    result = validate_with_tools(c, store, llm, store.policy, Events())
    assert result.tool_rounds == 2
    assert result.report.stock_snapshot.stock == {"WidgetA": 15, "WidgetB": 10}
    assert any(m["role"] == "tool" for m in llm.requests[1].messages)
    store.close()


@pytest.mark.parametrize(
    "responses",
    [
        [LLMResponse(content={"stock": {"WidgetA": 100}})] * 2,
        [response(["WidgetA"], name="pay_invoice")] * 2,
        [response("WidgetA")] * 2,
        [response(["WidgetB"])] * 2,
        [
            LLMResponse(
                tool_calls=[
                    LLMToolCall(
                        call_id="x", name="lookup_inventory", arguments={"items": ["WidgetA"]}
                    ),
                    LLMToolCall(
                        call_id="x", name="lookup_inventory", arguments={"items": ["WidgetA"]}
                    ),
                ]
            )
        ]
        * 2,
    ],
)
def test_bad_protocol_bounded_and_never_trusted(tmp_path, responses):
    store = SQLitePaymentStore(tmp_path / "db", "run")
    events = Events()
    result = validate_with_tools(candidate(), store, Scripted(responses), store.policy, events)
    assert result.error.code == "TOOL_PROTOCOL_ERROR"
    assert result.tool_rounds == 2
    assert result.report is None
    assert not events.events
    assert store.snapshot().stock["WidgetA"] == 15
    store.close()


def test_missing_after_second_round_is_error(tmp_path):
    store = SQLitePaymentStore(tmp_path / "db", "run")
    c = candidate()
    c = c.model_copy(
        update={
            "items": [c.items[0], c.items[0].model_copy(update={"item_name_normalized": "WidgetB"})]
        }
    )
    result = validate_with_tools(
        c, store, Scripted([response(["WidgetA"]), response(["WidgetA"])]), store.policy, Events()
    )
    assert result.error.code == "TOOL_PROTOCOL_ERROR"
    store.close()
