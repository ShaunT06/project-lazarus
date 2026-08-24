"""Mocked-transport tests - no real Razorpay API calls in CI. Covers the
429 retry/backoff behavior added after hitting Razorpay's test-mode rate
limit while creating the batch's real orders."""

import httpx
import pytest

from app.razorpay_client import RazorpayClient


def make_client(handler) -> RazorpayClient:
    client = RazorpayClient(key_id="rzp_test_fake", key_secret="fake_secret")
    client._client = httpx.Client(
        base_url="https://api.razorpay.com/v1",
        auth=("rzp_test_fake", "fake_secret"),
        transport=httpx.MockTransport(handler),
    )
    return client


def test_create_order_success():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/orders"
        return httpx.Response(200, json={"id": "order_abc123", "amount": 149900})

    client = make_client(handler)
    order = client.create_order(amount_inr=1499, receipt="case_1")
    assert order["id"] == "order_abc123"


def test_create_order_converts_rupees_to_paise():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "order_xyz"})

    client = make_client(handler)
    client.create_order(amount_inr=899.5, receipt="case_2")
    assert captured["body"]["amount"] == 89950
    assert captured["body"]["currency"] == "INR"


def test_create_order_retries_on_429_then_succeeds():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] < 3:
            return httpx.Response(429, headers={"Retry-After": "0"}, json={"error": "rate limited"})
        return httpx.Response(200, json={"id": "order_after_retry"})

    client = make_client(handler)
    order = client.create_order(amount_inr=500, receipt="case_3", max_retries=5)
    assert order["id"] == "order_after_retry"
    assert calls["count"] == 3


def test_create_order_raises_after_exhausting_retries():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "0"}, json={"error": "rate limited"})

    client = make_client(handler)
    with pytest.raises(httpx.HTTPStatusError):
        client.create_order(amount_inr=500, receipt="case_4", max_retries=1)


def test_list_orders_by_receipt_filters_and_maps():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "items": [
                    {"id": "order_1", "receipt": "case_a"},
                    {"id": "order_2", "receipt": "case_b"},
                    {"id": "order_3", "receipt": None},
                ]
            },
        )

    client = make_client(handler)
    mapping = client.list_orders_by_receipt()
    assert mapping == {"case_a": "order_1", "case_b": "order_2"}
