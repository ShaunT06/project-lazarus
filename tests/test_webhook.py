"""Signature verification and idempotency - the plumbing tests from
plan.md's testing strategy §1 (resend the same event twice; confirm dedup
survives a restart)."""

import hashlib
import hmac
import json

from fastapi.testclient import TestClient

from app.main import create_app

SECRET = "test_webhook_secret"


def make_client(tmp_path, secret: str = SECRET) -> TestClient:
    app = create_app(
        webhook_secret=secret,
        db_path=tmp_path / "events.db",
        audit_path=tmp_path / "audit.jsonl",
        database_url="",  # hermetic: never redirected by a developer's local .env
    )
    return TestClient(app)


def sign(body: bytes, secret: str = SECRET) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def test_valid_signature_new_event_is_processed(tmp_path):
    client = make_client(tmp_path)
    body = json.dumps({"id": "evt_1", "event": "payment.failed"}).encode()

    resp = client.post(
        "/webhooks/razorpay",
        content=body,
        headers={"X-Razorpay-Signature": sign(body), "x-razorpay-event-id": "evt_1"},
    )

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "duplicate": False}


def test_duplicate_event_id_is_deduped(tmp_path):
    client = make_client(tmp_path)
    body = json.dumps({"id": "evt_2", "event": "payment.failed"}).encode()
    headers = {"X-Razorpay-Signature": sign(body), "x-razorpay-event-id": "evt_2"}

    first = client.post("/webhooks/razorpay", content=body, headers=headers)
    second = client.post("/webhooks/razorpay", content=body, headers=headers)

    assert first.json()["duplicate"] is False
    assert second.json()["duplicate"] is True


def test_dedupe_survives_process_restart(tmp_path):
    db_path = tmp_path / "events.db"
    audit_path = tmp_path / "audit.jsonl"
    body = json.dumps({"id": "evt_3", "event": "payment.failed"}).encode()
    headers = {"X-Razorpay-Signature": sign(body), "x-razorpay-event-id": "evt_3"}

    app1 = create_app(
        webhook_secret=SECRET, db_path=db_path, audit_path=audit_path, database_url=""
    )
    TestClient(app1).post("/webhooks/razorpay", content=body, headers=headers)

    # Fresh app instance, same on-disk db - simulates a redeploy/crash-restart.
    app2 = create_app(
        webhook_secret=SECRET, db_path=db_path, audit_path=audit_path, database_url=""
    )
    resp = TestClient(app2).post("/webhooks/razorpay", content=body, headers=headers)

    assert resp.json()["duplicate"] is True


def test_invalid_signature_is_rejected(tmp_path):
    client = make_client(tmp_path)
    body = json.dumps({"id": "evt_4", "event": "payment.failed"}).encode()

    resp = client.post(
        "/webhooks/razorpay",
        content=body,
        headers={"X-Razorpay-Signature": "0" * 64, "x-razorpay-event-id": "evt_4"},
    )

    assert resp.status_code == 401


def test_missing_signature_header_is_rejected(tmp_path):
    client = make_client(tmp_path)
    body = json.dumps({"id": "evt_5"}).encode()

    resp = client.post("/webhooks/razorpay", content=body)

    assert resp.status_code == 400


def test_tampered_body_fails_signature_check(tmp_path):
    client = make_client(tmp_path)
    original = json.dumps({"id": "evt_6", "event": "payment.failed"}).encode()
    valid_signature = sign(original)
    tampered = json.dumps({"id": "evt_6", "event": "payment.captured"}).encode()

    resp = client.post(
        "/webhooks/razorpay",
        content=tampered,
        headers={"X-Razorpay-Signature": valid_signature, "x-razorpay-event-id": "evt_6"},
    )

    assert resp.status_code == 401


def test_missing_webhook_secret_configured_returns_500(tmp_path):
    client = make_client(tmp_path, secret="")
    body = json.dumps({"id": "evt_7"}).encode()

    resp = client.post(
        "/webhooks/razorpay",
        content=body,
        headers={"X-Razorpay-Signature": sign(body), "x-razorpay-event-id": "evt_7"},
    )

    assert resp.status_code == 500
