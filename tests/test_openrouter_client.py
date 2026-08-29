"""Mocked-transport tests - no real OpenRouter API calls in CI. Covers bugs
found on a live 50-case batch run against z-ai/glm-5.3-flash: a model
occasionally emits a JSON array instead of an object for tool arguments,
and the connection can be reset mid-request. Also covers a bug found on
the deployed /chat UI: a single chat() call's own internal retry loop
could alone consume a caller's whole time budget and get hard-killed by
Vercel's 60s function timeout with no graceful response."""

import json
import time

import httpx
import pytest

from app.openrouter_client import OpenRouterClient


def make_client(handler, **kwargs) -> OpenRouterClient:
    client = OpenRouterClient(api_key="sk-fake", model="fake/model", **kwargs)
    client._client = httpx.Client(
        base_url="https://openrouter.ai/api/v1",
        transport=httpx.MockTransport(handler),
    )
    return client


def _completion(tool_calls: list[dict] | None = None, content: str | None = None) -> dict:
    message: dict = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {"choices": [{"message": message}], "usage": {"cost": 0.000006}}


def test_non_dict_tool_arguments_are_coerced_to_empty_dict():
    # Observed live: generate_split_payment_link called with arguments
    # '["installments"]' - a JSON array, not an object. Every downstream
    # consumer assumes a dict; this must not crash the client.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_completion(
                tool_calls=[
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "generate_split_payment_link",
                            "arguments": json.dumps(["installments"]),
                        },
                    }
                ]
            ),
        )

    client = make_client(handler)
    resp = client.chat([{"role": "user", "content": "hi"}])

    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0].arguments == {}


def test_valid_dict_tool_arguments_pass_through_unchanged():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_completion(
                tool_calls=[
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "send_message",
                            "arguments": json.dumps({"body": "hello"}),
                        },
                    }
                ]
            ),
        )

    client = make_client(handler)
    resp = client.chat([{"role": "user", "content": "hi"}])

    assert resp.tool_calls[0].arguments == {"body": "hello"}


def test_connection_error_is_retried_then_succeeds():
    # Observed live: "[WinError 10054] An existing connection was forcibly
    # closed by the remote host" mid-request - transient, not a client bug.
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] < 3:
            raise httpx.ConnectError("connection reset", request=request)
        return httpx.Response(200, json=_completion(content="ok"))

    client = make_client(handler)
    resp = client.chat([{"role": "user", "content": "hi"}], max_retries=5)

    assert resp.content == "ok"
    assert calls["count"] == 3


def test_connection_error_raises_after_exhausting_retries():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection reset", request=request)

    client = make_client(handler)
    with pytest.raises(httpx.ConnectError):
        client.chat([{"role": "user", "content": "hi"}], max_retries=1)


def test_cost_usd_is_extracted_from_usage():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_completion(content="ok"))

    client = make_client(handler)
    resp = client.chat([{"role": "user", "content": "hi"}])

    assert resp.cost_usd == 0.000006


def test_already_expired_deadline_makes_no_request_at_all():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(200, json=_completion(content="ok"))

    client = make_client(handler)
    with pytest.raises(TimeoutError):
        client.chat([{"role": "user", "content": "hi"}], deadline=time.monotonic() - 1)

    assert calls["count"] == 0


def test_deadline_stops_retry_loop_promptly_instead_of_sleeping_past_it():
    # A persistently-retryable failure (429, not the daily-quota kind) would
    # normally sleep with growing backoff between attempts. A deadline must
    # cut that short rather than oversleeping past it and only then giving
    # up - this is what let one call alone burn a caller's whole budget.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "30"}, json={"error": "rate limited"})

    client = make_client(handler)
    deadline = time.monotonic() + 0.2  # expires well before a 30s Retry-After would

    start = time.monotonic()
    with pytest.raises(TimeoutError):
        client.chat([{"role": "user", "content": "hi"}], max_retries=5, deadline=deadline)
    elapsed = time.monotonic() - start

    assert elapsed < 2.0  # nowhere near the 30s Retry-After or 5-retry backoff total


def test_deadline_none_behaves_exactly_as_before():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_completion(content="ok"))

    client = make_client(handler)
    resp = client.chat([{"role": "user", "content": "hi"}])  # no deadline passed

    assert resp.content == "ok"
