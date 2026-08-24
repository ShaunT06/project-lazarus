"""Thin client for the Razorpay REST API (test mode). HTTP Basic Auth with
key_id/key_secret - no official SDK dependency, consistent with how this
project already talks to OpenRouter (httpx, not a vendor SDK).
"""

import time

import httpx


class RazorpayClient:
    def __init__(self, key_id: str, key_secret: str, base_url: str = "https://api.razorpay.com/v1"):
        self._client = httpx.Client(base_url=base_url, auth=(key_id, key_secret), timeout=30.0)

    def create_order(
        self, *, amount_inr: float, receipt: str, notes: dict | None = None, max_retries: int = 5
    ) -> dict:
        body = {
            "amount": round(amount_inr * 100),  # Razorpay amounts are in paise
            "currency": "INR",
            "receipt": receipt,
            "notes": notes or {},
        }
        for attempt in range(max_retries + 1):
            resp = self._client.post("/orders", json=body)
            if resp.status_code != 429:
                resp.raise_for_status()
                return resp.json()
            if attempt == max_retries:
                resp.raise_for_status()
            retry_after = float(resp.headers.get("Retry-After", 2 * (attempt + 1)))
            time.sleep(retry_after)
        raise RuntimeError("unreachable")  # loop always returns or raises

    def list_orders_by_receipt(self, count: int = 100) -> dict[str, str]:
        """Returns {receipt: order_id} for existing orders - used to adopt
        orders from an interrupted prior run instead of creating duplicates."""
        resp = self._client.get("/orders", params={"count": count})
        resp.raise_for_status()
        return {
            item["receipt"]: item["id"]
            for item in resp.json().get("items", [])
            if item.get("receipt")
        }

    def close(self) -> None:
        self._client.close()
