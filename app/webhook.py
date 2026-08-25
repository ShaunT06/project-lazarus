"""Razorpay webhook receiver.

Verifies X-Razorpay-Signature (HMAC-SHA256 over the raw request body),
dedupes on x-razorpay-event-id against a persistent store, then runs
processing (diagnosis -> strategy -> agent) inline before responding.

That used to be a FastAPI BackgroundTask fired after the response, on the
theory that Razorpay's delivery timeout shouldn't sit behind an LLM call.
Vercel's serverless Python runtime doesn't guarantee code scheduled after
the response is actually sent runs to completion once the function
returns, so a fire-and-forget task there can silently vanish - a "pipeline
ran" audit event is worth more than a fast ack we can't back up. One LLM
turn is a few seconds; that's an acceptable webhook response time here.

Scope note: only `payment.failed` events feed the agent pipeline right
now. True checkout abandonment (a cart with no payment attempt at all)
isn't a Razorpay webhook event - it needs separate session/client-side
tracking, not yet built. B2B receivables are synthetic and never arrive
via webhook. Field extraction from the payment entity (customer_id,
error_reason) is a best-effort mapping - reconcile against real webhook
payloads before the batch run.
"""

import hashlib
import hmac
import json
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request

from app.agent import run_case
from app.audit import AuditLogger
from app.customer_store import CustomerStore
from app.diagnosis import diagnose
from app.models import CaseContext
from app.strategy import StrategyEngine
from app.webhook_store import WebhookEventStore

_OUTREACH_TOOLS = {"send_message", "generate_payment_link", "generate_split_payment_link"}


def verify_signature(raw_body: bytes, signature: str, secret: str) -> bool:
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def _extract_case_context(
    event_id: str, payload: dict, customer_store: CustomerStore
) -> CaseContext | None:
    entity = payload.get("payload", {}).get("payment", {}).get("entity")
    if not entity:
        return None

    customer_id = (
        entity.get("customer_id")
        or entity.get("email")
        or entity.get("contact")
        or entity.get("id")
    )
    if not customer_id:
        return None

    error_code = entity.get("error_reason") or entity.get("error_code")
    amount_inr = (entity.get("amount") or 0) / 100  # Razorpay amounts are in paise

    customer_store.record_abandon_event(customer_id)
    profile = customer_store.get_profile(customer_id)

    return CaseContext(
        case_id=event_id,
        customer_id=customer_id,
        customer_ltv_inr=profile["ltv_inr"],
        abandons_last_7d=customer_store.abandons_last_7d(customer_id),
        marketing_opt_in=profile["marketing_opt_in"],
        hours_since_last_outreach=customer_store.hours_since_last_outreach(customer_id),
        error_code=error_code,
        cart_amount_inr=amount_inr,
        category="subscription_failure",
        is_synthetic=False,
    )


def _process_event(
    event_id: str,
    payload: dict,
    *,
    audit: AuditLogger,
    customer_store: CustomerStore,
    strategy_engine: StrategyEngine,
    openrouter_client_factory: Callable[[], Any],
    max_gate_corrections: int,
    notify_channel: str,
) -> None:
    event_type = payload.get("event")
    if event_type != "payment.failed":
        audit.log(event_id, "event_type_not_handled", {"event": event_type})
        return

    try:
        case = _extract_case_context(event_id, payload, customer_store)
        if case is None:
            audit.log(
                event_id,
                "pipeline_skipped",
                {"reason": "could not extract case context from payload"},
            )
            return

        cause_category = diagnose(case.error_code)
        case.extra["cause_category"] = cause_category
        strategy_result = strategy_engine.evaluate(case)

        client = openrouter_client_factory()
        try:
            result = run_case(
                case,
                cause_category,
                strategy_result,
                client,
                audit,
                max_corrections=max_gate_corrections,
                notify_channel=notify_channel,
            )
        finally:
            client.close()

        if any(a["tool"] in _OUTREACH_TOOLS for a in result.actions):
            customer_store.record_outreach_event(case.customer_id)

    except Exception as exc:  # must not raise into the webhook response - log and 200 anyway
        audit.log(event_id, "pipeline_error", {"error": str(exc)})


def build_webhook_router(
    *,
    store: WebhookEventStore,
    audit: AuditLogger,
    webhook_secret: str,
    customer_store: CustomerStore,
    strategy_engine: StrategyEngine,
    openrouter_client_factory: Callable[[], Any],
    max_gate_corrections: int = 3,
    notify_channel: str = "console",
) -> APIRouter:
    router = APIRouter()

    @router.post("/webhooks/razorpay")
    async def razorpay_webhook(
        request: Request,
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
            _process_event(
                event_id,
                payload,
                audit=audit,
                customer_store=customer_store,
                strategy_engine=strategy_engine,
                openrouter_client_factory=openrouter_client_factory,
                max_gate_corrections=max_gate_corrections,
                notify_channel=notify_channel,
            )

        return {"status": "ok", "duplicate": not is_new}

    return router
