"""Small xAI chat-completions adapter with bounded, observable transport retries."""

from __future__ import annotations

import json
import random
import time
from collections.abc import Callable
from typing import Any

import httpx
import jsonschema

from .errors import InvoiceAgentError
from .models import ErrorCode, ErrorInfo, LLMRequest, LLMResponse, LLMToolCall, TraceEvent


class LLMError(InvoiceAgentError):
    """Sanitized provider failure; never carries provider response bodies."""

    def __init__(self, info: ErrorInfo):
        super().__init__(info)


class XAIClient:
    def __init__(
        self,
        api_key: str,
        model: str = "grok-4.3",
        base_url: str = "https://api.x.ai/v1",
        timeout: float = 30,
        retries: int = 2,
        transport: httpx.BaseTransport | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        events: Any = None,
        run_id: str = "",
        random_value: Callable[[], float] = random.random,
        clock: Callable[[], float] = time.monotonic,
    ):
        if retries < 0 or timeout <= 0:
            raise ValueError("Retry count and timeout must be valid")
        self.model = model
        self.retries = retries
        self.sleeper = sleeper
        self.events = events
        self.run_id = run_id
        self.random_value = random_value
        self.clock = clock
        self.client = httpx.Client(
            base_url=base_url.rstrip("/") + "/",
            timeout=timeout,
            transport=transport,
            headers={"Authorization": f"Bearer {api_key}"},
        )

        self._event("provider", "model_client_initialized", {"model": self.model})

    def close(self) -> None:
        self.client.close()

    def complete(self, request: LLMRequest) -> LLMResponse:
        start = self.clock()
        status = "error"
        try:
            result = self._complete(request, start)
            status = "success"
            return result
        finally:
            self._event(
                request.phase,
                "model_call_finished",
                {
                    "status": status,
                    "elapsed_ms": round((self.clock() - start) * 1000, 2),
                },
            )

    def _complete(self, request: LLMRequest, start: float) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": request.messages,
            "max_tokens": request.max_output_tokens,
            "temperature": 0,
        }
        if request.tools:
            payload["tools"] = request.tools
            payload["tool_choice"] = "required"
        elif request.output_schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "invoice_agent_response",
                    "schema": request.output_schema,
                    "strict": True,
                },
            }
        response: httpx.Response | None = None
        for attempt in range(self.retries + 1):
            retry_after = 0.0
            self._event(request.phase, "model_request", {"attempt": attempt + 1})
            try:
                response = self.client.post("chat/completions", json=payload)
                if response.status_code in (408, 429) or response.status_code >= 500:
                    try:
                        retry_after = min(
                            5.0, max(0.0, float(response.headers.get("retry-after", "0")))
                        )
                    except ValueError:
                        retry_after = 0.0
                    raise httpx.TimeoutException("Transient provider response")
                if response.status_code >= 400:
                    raise LLMError(
                        ErrorInfo(
                            code=ErrorCode.PROVIDER_PERMANENT,
                            message=f"xAI rejected the request (HTTP {response.status_code}); check key, model and API configuration.",
                            fatal=True,
                        )
                    )
                break
            except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError):
                self._event(
                    request.phase,
                    "transport_retry" if attempt < self.retries else "transport_exhausted",
                    {"attempt": attempt + 1},
                )
                if attempt == self.retries:
                    raise LLMError(
                        ErrorInfo(
                            code=ErrorCode.PROVIDER_TRANSIENT,
                            message="xAI request failed after bounded transport retries.",
                            retryable=True,
                        )
                    ) from None
                self.sleeper(
                    max(retry_after, min(5.0, 0.5 * 2**attempt + self.random_value() * 0.1))
                )
        assert response is not None
        try:
            body = response.json()
            if not isinstance(body, dict):
                raise ValueError("Response body must be an object")
            usage = collect_usage(body.get("usage"))
            self._event(
                request.phase,
                "model_usage",
                {
                    "model": self.model,
                    "resolved_model": body.get("model"),
                    "usage": usage,
                },
            )
            if body.get("usage") is not None and not isinstance(body["usage"], dict):
                raise ValueError("Usage must be an object")
            choices = body["choices"]
            if not isinstance(choices, list) or len(choices) != 1:
                raise ValueError("Expected one response")
            choice = choices[0]
            if not isinstance(choice, dict):
                raise ValueError("Response choice must be an object")
            message = choice["message"]
            if not isinstance(message, dict):
                raise ValueError("Response message must be an object")
            finish = choice.get("finish_reason", "stop")
            if finish not in ("stop", "tool_calls") or message.get("refusal"):
                raise ValueError("Incomplete or refused response")
            calls = []
            raw_calls = message.get("tool_calls")
            if raw_calls is None:
                raw_calls = []
            if not isinstance(raw_calls, list):
                raise ValueError("Tool calls must be a list")
            for call in raw_calls:
                if not isinstance(call, dict) or not isinstance(call.get("function"), dict):
                    raise ValueError("Tool call and function must be objects")
                arguments = json.loads(call["function"]["arguments"])
                if not isinstance(arguments, dict):
                    raise ValueError("Tool arguments must be an object")
                allowed = {tool.get("function", {}).get("name") for tool in request.tools}
                if call["function"]["name"] not in allowed:
                    raise ValueError("Provider requested unknown tool")
                calls.append(
                    LLMToolCall(
                        call_id=call["id"], name=call["function"]["name"], arguments=arguments
                    )
                )
            if calls and message.get("content"):
                raise ValueError("Ambiguous structured and tool response")
            content = None
            if not calls:
                content = json.loads(message["content"])
                if not isinstance(content, dict):
                    raise ValueError("Structured response must be an object")
                if request.output_schema is not None:
                    jsonschema.validate(content, request.output_schema)
            elif not request.tools:
                raise ValueError("Unexpected tool response")
            result = LLMResponse(
                content=content,
                tool_calls=calls,
                finish_reason=finish,
                usage=usage,
                provider_request_id=body.get("id"),
                transport_retries=attempt,
            )
        except (
            ValueError,
            KeyError,
            TypeError,
            IndexError,
            jsonschema.ValidationError,
            jsonschema.SchemaError,
        ):
            raise LLMError(
                ErrorInfo(
                    code=ErrorCode.LLM_SCHEMA_ERROR,
                    message="xAI returned an invalid, incomplete, or refused response.",
                )
            ) from None
        self._event(
            request.phase,
            "model_response",
            {
                "model": self.model,
                "resolved_model": body.get("model"),
                "transport_retries": attempt,
                "usage": result.usage,
                "elapsed_ms": round((self.clock() - start) * 1000, 2),
            },
        )
        return result

    def _event(self, phase: str, event: str, payload: dict[str, Any]) -> None:
        if self.events is not None:
            self.events.emit(
                TraceEvent(run_id=self.run_id, stage=phase, event=event, payload=payload)
            )


def collect_usage(raw: Any) -> dict[str, int]:
    """Keep only known numeric accounting fields; never log arbitrary provider metadata."""
    if not isinstance(raw, dict):
        return {}
    result: dict[str, int] = {}
    for target, aliases in {
        "prompt_tokens": ("prompt_tokens", "input_tokens"),
        "completion_tokens": ("completion_tokens", "output_tokens"),
        "total_tokens": ("total_tokens",),
        "cached_prompt_tokens": ("cached_prompt_tokens",),
        "reasoning_tokens": ("reasoning_tokens",),
        "cost_in_usd_ticks": ("cost_in_usd_ticks",),
    }.items():
        for alias in aliases:
            value = raw.get(alias)
            if type(value) is int and value >= 0:
                result[target] = value
                break
    for target, containers, key in (
        (
            "cached_prompt_tokens",
            ("prompt_tokens_details", "input_tokens_details"),
            "cached_tokens",
        ),
        (
            "reasoning_tokens",
            ("completion_tokens_details", "output_tokens_details"),
            "reasoning_tokens",
        ),
    ):
        for container in containers:
            details = raw.get(container)
            value = details.get(key) if isinstance(details, dict) else None
            if type(value) is int and value >= 0 and target not in result:
                result[target] = value
    prompt = result.get("prompt_tokens")
    output = result.get("completion_tokens")
    total = result.get("total_tokens")
    reasoning = result.get("reasoning_tokens", 0)
    # Chat Completions may report reasoning separately; normalize billed output.
    if (
        prompt is not None
        and output is not None
        and total is not None
        and reasoning > 0
        and total == prompt + output + reasoning
    ):
        result["completion_tokens"] = output + reasoning
    return result
