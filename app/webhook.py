"""Razorpay webhook receiver.

Verifies X-Razorpay-Signature (HMAC-SHA256 over the raw request body),
dedupes on x-razorpay-event-id against a persistent store, and returns 2xx
immediately - real processing (diagnosis -> strategy -> agent) is handed to
a background task so Razorpay's delivery timeout is never in the critical
path.

NOTE: full pipeline wiring (state serialization / CaseContext hydration
from the payload) is a later PR - this one is scoped to receipt,
verification, and idempotency, matching plan.md's phase split.
"""

import hashlib
import hmac
import json

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request

from app.audit import AuditLogger
from app.webhook_store import WebhookEventStore


def verify_signature(raw_body: bytes, signature: str, secret: str) -> bool:
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def _process_event(event_id: str, payload: dict, audit: AuditLogger) -> None:
    audit.log(event_id, "webhook_processed", {"event": payload.get("event")})


def build_webhook_router(
    *, store: WebhookEventStore, audit: AuditLogger, webhook_secret: str
) -> APIRouter:
    router = APIRouter()

    @router.post("/webhooks/razorpay")
    async def razorpay_webhook(
        request: Request,
        background_tasks: BackgroundTasks,
        x_razorpay_signature: str | None = Header(default=None, alias="X-Razorpay-Signature"),
        x_razorpay_event_id: str | None = Header(default=None, alias="x-razorpay-event-id"),
    ):
        if not webhook_secret:
            raise HTTPException(status_code=500, detail="webhook secret not configured")

        raw_body = await request.body()

        if not x_razorpay_signature:
            raise HTTPException(status_code=400, detail="missing X-Razorpay-Signature header")

        if not verify_signature(raw_body, x_razorpay_signature, webhook_secret):
            raise HTTPException(status_code=401, detail="invalid signature")

        try:
            payload = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="invalid JSON body") from exc

        event_id = x_razorpay_event_id or payload.get("id")
        if not event_id:
            raise HTTPException(status_code=400, detail="missing event id")

        is_new = store.mark_processed_if_new(event_id)
        audit.log(event_id, "webhook_received", {"event": payload.get("event"), "is_new": is_new})

        if is_new:
            background_tasks.add_task(_process_event, event_id, payload, audit)

        return {"status": "ok", "duplicate": not is_new}

    return router
