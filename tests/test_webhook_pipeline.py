"""End-to-end: webhook receipt -> CaseContext extraction -> diagnosis ->
strategy -> agent, using a scripted fake OpenRouter client so no real API
call happens. FastAPI's TestClient runs BackgroundTasks synchronously
within the request cycle, so we can assert on the audit trail right after
client.post() returns.
"""

import hashlib
import hmac
import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from app.openrouter_client import LLMResponse, ToolCall

SECRET = "test_secret"
STRATEGY_CONFIG = Path("config/strategy.example.json")


class FakeOpenRouterClient:
    """Drop-in for OpenRouterClient - returns queued scripted responses."""

    next_responses: list[LLMResponse] = []

    def __init__(self, *args, **kwargs):
        self._responses = list(FakeOpenRouterClient.next_responses)

    def chat(self, messages, tools=None):
        if not self._responses:
            return LLMResponse(
                content="Done.",
                tool_calls=[],
                raw_message={"role": "assistant", "content": "Done."},
            )
        return self._responses.pop(0)

    def close(self) -> None:
        pass


def make_tool_call(name: str, arguments: dict) -> LLMResponse:
    return LLMResponse(
        content=None,
        tool_calls=[ToolCall(id="call_1", name=name, arguments=arguments)],
        raw_message={
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(arguments)},
                }
            ],
        },
    )


def make_payment_failed_payload(
    event_id: str, customer_id: str, error_reason: str, amount_paise: int
) -> dict:
    return {
        "id": event_id,
        "event": "payment.failed",
        "payload": {
            "payment": {
                "entity": {
                    "id": f"pay_{event_id}",
                    "customer_id": customer_id,
                    "amount": amount_paise,
                    "error_reason": error_reason,
                }
            }
        },
    }


def sign(body: bytes) -> str:
    return hmac.new(SECRET.encode("utf-8"), body, hashlib.sha256).hexdigest()


def make_client(tmp_path, responses: list[LLMResponse] | None = None) -> tuple[TestClient, Path]:
    FakeOpenRouterClient.next_responses = responses or []
    app = create_app(
        webhook_secret=SECRET,
        db_path=tmp_path / "events.db",
        audit_path=tmp_path / "audit.jsonl",
        customer_db_path=tmp_path / "customers.db",
        strategy_config_path=STRATEGY_CONFIG,
        openrouter_client_factory=FakeOpenRouterClient,
        database_url="",  # hermetic: never redirected by a developer's local .env
    )
    return TestClient(app), tmp_path / "audit.jsonl"


def _events_for_case(audit_path: Path, case_id: str) -> list[str]:
    lines = audit_path.read_text(encoding="utf-8").strip().splitlines()
    records = [json.loads(line) for line in lines]
    return [r["event_type"] for r in records if r["case_id"] == case_id]


def test_payment_failed_runs_full_pipeline_and_executes_approved_tool(tmp_path):
    responses = [
        make_tool_call("send_message", {"body": "We noticed an issue with your card."}),
        LLMResponse(
            content="Done.", tool_calls=[], raw_message={"role": "assistant", "content": "Done."}
        ),
    ]
    client, audit_path = make_client(tmp_path, responses)

    payload = make_payment_failed_payload("evt_p1", "cust_a", "expired_card", 89900)
    body = json.dumps(payload).encode()
    resp = client.post(
        "/webhooks/razorpay",
        content=body,
        headers={"X-Razorpay-Signature": sign(body), "x-razorpay-event-id": "evt_p1"},
    )

    assert resp.status_code == 200
    event_types = _events_for_case(audit_path, "evt_p1")
    assert "diagnosis" in event_types
    assert "strategy_result" in event_types
    assert "tool_approved_executed" in event_types


def test_non_payment_failed_event_skips_pipeline(tmp_path):
    client, audit_path = make_client(tmp_path)

    payload = {"id": "evt_p2", "event": "payment.captured", "payload": {}}
    body = json.dumps(payload).encode()
    resp = client.post(
        "/webhooks/razorpay",
        content=body,
        headers={"X-Razorpay-Signature": sign(body), "x-razorpay-event-id": "evt_p2"},
    )

    assert resp.status_code == 200
    assert _events_for_case(audit_path, "evt_p2") == ["webhook_received", "event_type_not_handled"]


def test_unrecognized_payload_shape_is_skipped_not_crashed(tmp_path):
    client, audit_path = make_client(tmp_path)

    payload = {"id": "evt_p3", "event": "payment.failed"}  # no payload.payment.entity
    body = json.dumps(payload).encode()
    resp = client.post(
        "/webhooks/razorpay",
        content=body,
        headers={"X-Razorpay-Signature": sign(body), "x-razorpay-event-id": "evt_p3"},
    )

    assert resp.status_code == 200
    assert "pipeline_skipped" in _events_for_case(audit_path, "evt_p3")


def test_third_abandonment_in_a_week_hits_hard_stop(tmp_path):
    client, audit_path = make_client(tmp_path)

    for i in range(1, 4):
        payload = make_payment_failed_payload(
            f"evt_ab_{i}", "cust_fatigued", "insufficient_funds", 50000
        )
        body = json.dumps(payload).encode()
        client.post(
            "/webhooks/razorpay",
            content=body,
            headers={"X-Razorpay-Signature": sign(body), "x-razorpay-event-id": f"evt_ab_{i}"},
        )

    third_case_events = _events_for_case(audit_path, "evt_ab_3")
    assert "hard_stop" in third_case_events
    assert "tool_approved_executed" not in third_case_events
