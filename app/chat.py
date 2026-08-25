"""API for the customer-facing chat UI (/chat). A judge/customer triggers a
simulated payment failure, then talks to Lazarus directly - the same
diagnosis -> strategy -> agent -> policy-gate loop as the webhook pipeline
(diagnose(), StrategyEngine.evaluate(), the shared agent turn-loop), just
re-entered on every reply instead of run once.

The failure trigger is simulated by design (no live Razorpay webhook
delivery needed for a demo): /api/chat/start builds a CaseContext directly
and calls the pipeline in-process, rather than replaying through
webhook.py's HTTP signature-verification layer. That's a deliberate choice,
not a shortcut dressed up as more - HMAC verification exists to prove a
payload came from Razorpay; re-signing and re-checking our own in-process
call would prove nothing and would just be security theater. Every case
created here is logged plainly (`is_synthetic: true`, `data_source:
"simulated_by_customer_ui"`) so nothing overstates itself as a real
gateway failure.
"""

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.agent import continue_conversation, start_conversation
from app.audit import AuditLogger
from app.conversation_store import ConversationStore, TursoConversationStore
from app.customer_store import CustomerStore, TursoCustomerStore
from app.diagnosis import diagnose
from app.models import CaseContext
from app.openrouter_client import QuotaExhausted
from app.strategy import StrategyEngine
from app.strategy_store import StrategyConfigStore, TursoStrategyConfigStore

_LLM_UNAVAILABLE_MESSAGE = (
    "Lazarus can't reach the language model right now (it may be rate-limited - "
    "the free-tier OpenRouter quota is shared across the whole project). "
    "The diagnosis and strategy bounds for this case were still computed "
    "deterministically; try sending a message again in a moment."
)


def _llm_error_detail(exc: Exception) -> str:
    """QuotaExhausted gets a specific, accurate message (the daily cap
    resets once a day, not "in a moment") - anything else (a transient
    network/provider hiccup) gets the generic retry-soon message."""
    if isinstance(exc, QuotaExhausted):
        when = exc.reset_at.strftime("%H:%M UTC") if exc.reset_at else "the next daily reset"
        return (
            "Lazarus has hit OpenRouter's free-tier daily quota (shared across the whole "
            f"project) - it won't recover until {when}, so retrying now won't help. The "
            "diagnosis and strategy bounds for this case were still computed "
            "deterministically; only the agent's negotiation is unavailable right now."
        )
    return _LLM_UNAVAILABLE_MESSAGE


SCENARIOS: list[dict[str, Any]] = [
    {
        "id": "high_ltv_insufficient_funds",
        "label": "Subscription renewal - insufficient funds (high-LTV customer)",
        "category": "subscription_failure",
        "error_code": "insufficient_funds",
        "cart_amount_inr": 1499,
        "customer_ltv_inr": 32000,
    },
    {
        "id": "card_expired",
        "label": "Subscription renewal - expired card",
        "category": "subscription_failure",
        "error_code": "expired_card",
        "cart_amount_inr": 899,
        "customer_ltv_inr": 4000,
    },
    {
        "id": "issuer_down",
        "label": "Checkout - issuer bank temporarily down",
        "category": "subscription_failure",
        "error_code": "issuer_down",
        "cart_amount_inr": 2199,
        "customer_ltv_inr": 12000,
    },
    {
        "id": "checkout_abandoned",
        "label": "Checkout abandoned before payment was attempted",
        "category": "checkout_abandonment",
        "error_code": None,
        "cart_amount_inr": 3499,
        "customer_ltv_inr": 6000,
    },
    {
        "id": "b2b_receivable",
        "label": "B2B invoice overdue (receivable)",
        "category": "receivable",
        "error_code": None,
        "cart_amount_inr": 45000,
        "customer_ltv_inr": 180000,
    },
    {
        "id": "repeat_abandoner",
        "label": "Repeat abandoner - 3rd failure this week (hard-stop demo)",
        "category": "subscription_failure",
        "error_code": "insufficient_funds",
        "cart_amount_inr": 1499,
        "customer_ltv_inr": 9000,
        "force_abandons_last_7d": 3,
    },
]

_SCENARIOS_BY_ID = {s["id"]: s for s in SCENARIOS}


class StartRequest(BaseModel):
    scenario_id: str


class MessageRequest(BaseModel):
    body: str


def _display_from_actions(store, case_id: str, actions: list[dict], rejections: list[dict]) -> None:
    for r in rejections:
        store.add_display_message(
            case_id,
            role="system",
            kind="gate_rejected",
            body=r["reason"],
            meta={"tool": r["tool"], "arguments": r["arguments"]},
        )
    for a in actions:
        tool, args, result = a["tool"], a["arguments"], a["result"]
        if tool == "send_message":
            store.add_display_message(case_id, role="agent", kind="text", body=args.get("body"))
        elif tool == "generate_payment_link":
            store.add_display_message(
                case_id,
                role="agent",
                kind="payment_link",
                body=None,
                meta={
                    "link": result.get("link"),
                    "discount_pct": result.get("discount_pct", 0),
                },
            )
        elif tool == "generate_split_payment_link":
            store.add_display_message(
                case_id,
                role="agent",
                kind="payment_link",
                body=None,
                meta={
                    "link": result.get("link"),
                    "installments": result.get("installments"),
                    "split": True,
                },
            )
        elif tool == "schedule_retry":
            store.add_display_message(
                case_id,
                role="system",
                kind="text",
                body=f"Lazarus scheduled an automatic retry in {args.get('delay_hours')}h.",
            )
        elif tool == "update_customer_record":
            pass  # internal CRM note - dashboard/audit only, not customer-facing


def build_chat_router(
    *,
    conversation_store: ConversationStore | TursoConversationStore,
    customer_store: CustomerStore | TursoCustomerStore,
    audit: AuditLogger,
    strategy_store: StrategyConfigStore | TursoStrategyConfigStore,
    openrouter_client_factory,
    max_gate_corrections: int = 3,
    notify_channel: str = "console",
) -> APIRouter:
    router = APIRouter(prefix="/api/chat")

    @router.get("/scenarios")
    def list_scenarios():
        return [{k: v for k, v in s.items() if k != "force_abandons_last_7d"} for s in SCENARIOS]

    @router.post("/start")
    def start(req: StartRequest):
        scenario = _SCENARIOS_BY_ID.get(req.scenario_id)
        if scenario is None:
            raise HTTPException(status_code=404, detail="unknown scenario_id")

        case_id = f"chat_{uuid.uuid4().hex[:10]}"
        customer_id = f"chat_cust_{uuid.uuid4().hex[:8]}"

        if scenario.get("force_abandons_last_7d"):
            customer_store.record_abandon_event(customer_id)
            customer_store.record_abandon_event(customer_id)

        customer_store.record_abandon_event(customer_id)
        abandons = customer_store.abandons_last_7d(customer_id)

        case = CaseContext(
            case_id=case_id,
            customer_id=customer_id,
            customer_ltv_inr=scenario["customer_ltv_inr"],
            abandons_last_7d=abandons,
            marketing_opt_in=True,
            hours_since_last_outreach=999,
            error_code=scenario["error_code"],
            cart_amount_inr=scenario["cart_amount_inr"],
            category=scenario["category"],
            is_synthetic=True,
            extra={
                "data_source": "simulated_by_customer_ui",
                "scenario_id": scenario["id"],
            },
        )

        cause_category = diagnose(case.error_code)
        case.extra["cause_category"] = cause_category

        strategy_engine = StrategyEngine(strategy_store.get())
        strategy_result = strategy_engine.evaluate(case)

        client = openrouter_client_factory()
        try:
            try:
                result = start_conversation(
                    case,
                    cause_category,
                    strategy_result,
                    client,
                    audit,
                    max_corrections=max_gate_corrections,
                    notify_channel=notify_channel,
                )
            except Exception as exc:
                audit.log(case_id, "pipeline_error", {"error": str(exc)})
                raise HTTPException(status_code=503, detail=_llm_error_detail(exc)) from exc
        finally:
            client.close()

        conversation_store.create(case, cause_category, strategy_result, result["messages"])
        conversation_store.update_state(
            case_id,
            llm_messages=result["messages"],
            corrections=result["corrections"],
            approved_discount_pct=result["approved_discount_pct"],
            gate_exhausted=result["gate_exhausted"],
        )

        if result["hard_stop"]:
            conversation_store.add_display_message(
                case_id,
                role="system",
                kind="hard_stop",
                body=strategy_result.hard_stop_reason,
            )
        else:
            if any(a["tool"] != "update_customer_record" for a in result["actions"]):
                customer_store.record_outreach_event(customer_id)
            _display_from_actions(
                conversation_store, case_id, result["actions"], result["rejections"]
            )

        return {
            "case_id": case_id,
            "case": {
                "category": case.category,
                "cause_category": cause_category,
                "cart_amount_inr": case.cart_amount_inr,
                "customer_ltv_inr": case.customer_ltv_inr,
                "matched_rule_id": strategy_result.matched_rule_id,
                "allowed_actions": strategy_result.allowed_actions,
                "max_discount_pct": strategy_result.max_discount_pct,
            },
            "messages": conversation_store.get_display_messages(case_id),
        }

    @router.post("/{case_id}/message")
    def send_message(case_id: str, req: MessageRequest):
        convo = conversation_store.get(case_id)
        if convo is None:
            raise HTTPException(status_code=404, detail="unknown case_id")
        if convo["hard_stop"]:
            raise HTTPException(
                status_code=409, detail="this case was hard-stopped; no conversation to continue"
            )

        conversation_store.add_display_message(case_id, role="customer", kind="text", body=req.body)

        client = openrouter_client_factory()
        try:
            try:
                result = continue_conversation(
                    case=convo["case"],
                    strategy=convo["strategy"],
                    client=client,
                    audit=audit,
                    messages=convo["llm_messages"],
                    corrections=convo["corrections"],
                    approved_discount_pct=convo["approved_discount_pct"],
                    customer_message=req.body,
                    max_corrections=max_gate_corrections,
                    notify_channel=notify_channel,
                )
            except Exception as exc:
                audit.log(case_id, "pipeline_error", {"error": str(exc)})
                raise HTTPException(status_code=503, detail=_llm_error_detail(exc)) from exc
        finally:
            client.close()

        conversation_store.update_state(
            case_id,
            llm_messages=result["messages"],
            corrections=result["corrections"],
            approved_discount_pct=result["approved_discount_pct"],
            gate_exhausted=result["gate_exhausted"],
        )
        if any(a["tool"] != "update_customer_record" for a in result["actions"]):
            customer_store.record_outreach_event(convo["case"].customer_id)
        _display_from_actions(conversation_store, case_id, result["actions"], result["rejections"])

        return {"messages": conversation_store.get_display_messages(case_id)}

    @router.get("/{case_id}")
    def get_conversation(case_id: str):
        convo = conversation_store.get(case_id)
        if convo is None:
            raise HTTPException(status_code=404, detail="unknown case_id")
        return {
            "case_id": case_id,
            "hard_stop": convo["hard_stop"],
            "hard_stop_reason": convo["hard_stop_reason"],
            "gate_exhausted": convo["gate_exhausted"],
            "messages": conversation_store.get_display_messages(case_id),
        }

    return router
