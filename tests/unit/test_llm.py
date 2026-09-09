import json

import httpx
import pytest

from invoice_agent.llm import LLMError, XAIClient
from invoice_agent.models import ErrorCode, LLMRequest

SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": False,
}


def envelope(content='{"ok":true}', **message):
    return {
        "choices": [{"message": {"content": content, **message}, "finish_reason": "stop"}],
        "id": "req-1",
    }


def request(**kwargs):
    return LLMRequest(
        phase="test", messages=[{"role": "user", "content": "test"}], output_schema=SCHEMA, **kwargs
    )


def test_transport_payload_and_schema_success():
    def handler(req):
        payload = json.loads(req.content)
        assert req.headers["authorization"] == "Bearer sentinel-key"
        assert payload["model"] == "explicit-model"
        assert payload["response_format"]["json_schema"]["schema"] == SCHEMA
        return httpx.Response(200, json=envelope())

    client = XAIClient(
        "sentinel-key", model="explicit-model", transport=httpx.MockTransport(handler)
    )
    assert client.complete(request()).content == {"ok": True}
    client.close()


@pytest.mark.parametrize("content", ["not json", "{}", '{"ok":true,"extra":1}', "[]"])
def test_invalid_schema_fails_once(content):
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(200, json=envelope(content))

    client = XAIClient("key", transport=httpx.MockTransport(handler))
    with pytest.raises(LLMError) as error:
        client.complete(request())
    assert error.value.code == ErrorCode.LLM_SCHEMA_ERROR
    assert len(calls) == 1
    client.close()


@pytest.mark.parametrize("status", [408, 429, 500, 503])
def test_transient_retries_exactly_three_times(status):
    calls, delays = [], []

    def handler(req):
        calls.append(req)
        return httpx.Response(status, text="secret provider body", headers={"retry-after": "999"})

    client = XAIClient(
        "sentinel-key",
        transport=httpx.MockTransport(handler),
        sleeper=delays.append,
        random_value=lambda: 0,
    )
    with pytest.raises(LLMError) as error:
        client.complete(request())
    assert error.value.code == ErrorCode.PROVIDER_TRANSIENT
    assert len(calls) == 3
    assert delays == [5, 5]
    assert "secret provider body" not in str(error.value)
    assert "sentinel-key" not in str(error.value)
    client.close()


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_permanent_failures_never_retry(status):
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(status, text="private body")

    client = XAIClient("key", transport=httpx.MockTransport(handler))
    with pytest.raises(LLMError) as error:
        client.complete(request())
    assert error.value.info.fatal
    assert len(calls) == 1
    client.close()


def test_tool_call_id_and_arguments_survive():
    tool = {
        "type": "function",
        "function": {"name": "lookup_inventory", "parameters": {"type": "object"}},
    }
    message = envelope(
        None,
        tool_calls=[
            {
                "id": "call-7",
                "function": {"name": "lookup_inventory", "arguments": '{"items":["WidgetA"]}'},
            }
        ],
    )
    client = XAIClient(
        "key", transport=httpx.MockTransport(lambda _: httpx.Response(200, json=message))
    )
    result = client.complete(request(tools=[tool]))
    assert result.tool_calls[0].call_id == "call-7"
    assert result.tool_calls[0].arguments == {"items": ["WidgetA"]}
    client.close()


def test_unknown_tool_is_schema_failure():
    message = envelope(
        None, tool_calls=[{"id": "x", "function": {"name": "exec", "arguments": "{}"}}]
    )
    client = XAIClient(
        "key", transport=httpx.MockTransport(lambda _: httpx.Response(200, json=message))
    )
    with pytest.raises(LLMError):
        client.complete(
            request(tools=[{"type": "function", "function": {"name": "lookup_inventory"}}])
        )
    client.close()


def test_timeout_then_success_has_separate_retry_count():
    calls, delays = [], []

    def handler(req):
        calls.append(req)
        if len(calls) == 1:
            raise httpx.ReadTimeout("secret", request=req)
        return httpx.Response(200, json=envelope())

    client = XAIClient(
        "key", transport=httpx.MockTransport(handler), sleeper=delays.append, random_value=lambda: 0
    )
    assert client.complete(request()).transport_retries == 1
    assert delays == [0.5]
    client.close()


@pytest.mark.parametrize(
    "body",
    [
        {"choices": []},
        {"choices": [{"message": {"content": "{}"}, "finish_reason": "length"}]},
        envelope(refusal="Cannot respond"),
        envelope(
            None,
            tool_calls=[{"id": "x", "function": {"name": "lookup_inventory", "arguments": "[]"}}],
        ),
        envelope(
            '{"ok":true}',
            tool_calls=[{"id": "x", "function": {"name": "lookup_inventory", "arguments": "{}"}}],
        ),
    ],
)
def test_invalid_response_envelopes(body):
    client = XAIClient(
        "key", transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body))
    )
    tools = [{"type": "function", "function": {"name": "lookup_inventory"}}]
    with pytest.raises(LLMError):
        client.complete(request(tools=tools))
    client.close()


def test_observable_retry_and_elapsed_duration():
    events, delays, calls = [], [], []

    class Sink:
        def emit(self, event):
            events.append(event)

    def handler(req):
        assert events[-1].event == "model_request"
        calls.append(req)
        if len(calls) == 1:
            return httpx.Response(429, headers={"retry-after": "invalid"})
        return httpx.Response(200, json=envelope())

    ticks = iter([10.0, 10.5, 10.5])
    client = XAIClient(
        "key",
        transport=httpx.MockTransport(handler),
        events=Sink(),
        sleeper=delays.append,
        clock=lambda: next(ticks),
        random_value=lambda: 0,
    )
    client.complete(request())
    assert [event.event for event in events] == [
        "model_client_initialized",
        "model_request",
        "transport_retry",
        "model_request",
        "model_usage",
        "model_response",
        "model_call_finished",
    ]
    assert events[-1].payload["elapsed_ms"] == 500
    client.close()


@pytest.mark.parametrize(
    "body",
    [
        [],
        {"choices": {}},
        {"choices": [[]]},
        {"choices": [{"message": []}]},
        {"choices": [{"message": {"tool_calls": {"bad": True}}}]},
        {"choices": [{"message": {"tool_calls": [[]]}}]},
        {**envelope(), "usage": ["invalid"]},
    ],
)
def test_wrong_envelope_types_return_typed_schema_error(body):
    client = XAIClient(
        "key", transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body))
    )
    with pytest.raises(LLMError) as error:
        client.complete(request())
    assert error.value.code == ErrorCode.LLM_SCHEMA_ERROR
    client.close()


def test_usage_survives_schema_failure_and_secret_redaction():
    from invoice_agent.output import EventCollector

    events = EventCollector("r")
    body = envelope("not JSON")
    body["usage"] = {
        "prompt_tokens": 100,
        "completion_tokens": 20,
        "total_tokens": 120,
        "prompt_tokens_details": {"cached_tokens": 30},
        "completion_tokens_details": {"reasoning_tokens": 5},
        "cost_in_usd_ticks": 50000,
        "access_token": "never-emit-this",
    }
    client = XAIClient(
        "test",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body)),
        events=events,
    )
    try:
        with pytest.raises(LLMError):
            client.complete(request())
    finally:
        client.close()
    usage = next(e.payload["usage"] for e in events.events if e.event == "model_usage")
    assert usage == {
        "prompt_tokens": 100,
        "completion_tokens": 20,
        "total_tokens": 120,
        "cached_prompt_tokens": 30,
        "reasoning_tokens": 5,
        "cost_in_usd_ticks": 50000,
    }
    assert events.events[-1].event == "model_call_finished"
    assert events.events[-1].payload["status"] == "error"
    assert events.events[-1].payload["elapsed_ms"] >= 0
    assert "never-emit-this" not in str(events.events)


@pytest.mark.parametrize("output,total,expected", [(9, 135, 103), (103, 135, 103)])
def test_reasoning_usage_normalizes_chat_and_responses_without_double_count(
    output, total, expected
):
    from invoice_agent.llm import collect_usage

    usage = collect_usage(
        {
            "prompt_tokens": 32,
            "completion_tokens": output,
            "total_tokens": total,
            "completion_tokens_details": {"reasoning_tokens": 94},
        }
    )
    assert usage["completion_tokens"] == expected
    assert usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"]
