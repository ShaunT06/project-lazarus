"""The ReAct decision agent. Reasons over case + bounds, decides which tool
to call and how to phrase it. Never decides discount % or retry count itself
- those come from the strategy engine and are re-checked by the policy gate
on every single proposed call.

Loop shape:
  ask LLM -> for each proposed tool call, gate it -> approved: execute,
  append result; rejected: append error result, count a correction -> if
  corrections exceed the cap, stop and fall back to the deterministic
  default action (logged as gate_exhausted, never a silent catch) -> repeat
  until the LLM stops calling tools or the cap is hit.
"""

from app.audit import AuditLogger
from app.models import CaseContext, RunResult, StrategyResult
from app.openrouter_client import OpenRouterClient
from app.policy_gate import validate
from app.tools import TOOL_SCHEMAS, execute

MAX_TURNS = 8  # hard ceiling independent of the correction cap - never loop forever


SYSTEM_PROMPT_TEMPLATE = """You are Lazarus, a revenue-recovery agent acting for a merchant.

You operate strictly inside pre-approved bounds set by the merchant's strategy
engine. You cannot exceed them under any circumstance, even if it seems
reasonable - a deterministic gate checks every tool call you propose and will
reject anything outside these bounds, explaining why. If rejected, correct
your next proposal; do not repeat a rejected call.

Case:
- category: {category}
- diagnosed cause: {cause_category}
- cart amount (INR): {cart_amount_inr}

Bounds you must operate within:
- allowed actions: {allowed_actions}
- max discount: {max_discount_pct}%
- max retries: {max_retries}
- cooldown: {cooldown_hours}h since last outreach

Rules:
- Write all messages in English only. No other language, no code-switching.
- Never state a discount, offer, or number in a message you have not already
  gotten approved via a tool call.
- If nothing appropriate can be done within bounds, call send_message with a
  neutral, low-pressure nudge and stop.
- Take one action, then stop calling tools once the case is handled.
"""


def _build_system_prompt(case: CaseContext, cause_category: str, strategy: StrategyResult) -> str:
    return SYSTEM_PROMPT_TEMPLATE.format(
        category=case.category,
        cause_category=cause_category,
        cart_amount_inr=case.cart_amount_inr,
        allowed_actions=", ".join(strategy.allowed_actions) or "(none)",
        max_discount_pct=strategy.max_discount_pct,
        max_retries=strategy.max_retries,
        cooldown_hours=strategy.cooldown_hours,
    )


def _fallback_message(case: CaseContext) -> dict:
    return {
        "body": (
            "We noticed an issue completing your recent order. "
            "Reply if you'd like help finishing checkout."
        )
    }


def run_case(
    case: CaseContext,
    cause_category: str,
    strategy: StrategyResult,
    client: OpenRouterClient,
    audit: AuditLogger,
    *,
    max_corrections: int | None = None,
    notify_channel: str = "console",
) -> RunResult:
    audit.log(case.case_id, "diagnosis", {"cause_category": cause_category})
    audit.log(
        case.case_id,
        "strategy_result",
        strategy.model_dump(),
    )

    if strategy.hard_stop:
        audit.log(case.case_id, "hard_stop", {"reason": strategy.hard_stop_reason})
        return RunResult(case_id=case.case_id, actions=[], gate_exhausted=False)

    cap = max_corrections if max_corrections is not None else 3
    messages: list[dict] = [
        {"role": "system", "content": _build_system_prompt(case, cause_category, strategy)},
        {"role": "user", "content": f"Handle case {case.case_id} now."},
    ]

    actions: list[dict] = []
    corrections = 0
    approved_discount_this_run = 0.0

    for _turn in range(MAX_TURNS):
        response = client.chat(messages, tools=TOOL_SCHEMAS)
        audit.log(
            case.case_id,
            "llm_turn",
            {
                "content": response.content,
                "tool_calls": [tc.__dict__ for tc in response.tool_calls],
            },
        )

        if not response.tool_calls:
            audit.log(case.case_id, "agent_finished_no_action", {"text": response.content})
            break

        messages.append(response.raw_message)
        tool_result_messages = []

        for tc in response.tool_calls:
            decision = validate(
                tc.name,
                tc.arguments,
                strategy,
                case,
                approved_discount_pct_this_run=approved_discount_this_run,
            )
            if decision.approved:
                result = execute(tc.name, tc.arguments, notify_channel=notify_channel)
                audit.log(
                    case.case_id,
                    "tool_approved_executed",
                    {"tool": tc.name, "arguments": tc.arguments, "result": result},
                )
                actions.append({"tool": tc.name, "arguments": tc.arguments, "result": result})
                if tc.name in ("generate_payment_link", "generate_split_payment_link"):
                    approved_discount_this_run = max(
                        approved_discount_this_run, float(tc.arguments.get("discount_pct", 0) or 0)
                    )
                tool_result_messages.append(
                    {"role": "tool", "tool_call_id": tc.id, "content": _to_content(result)}
                )
            else:
                corrections += 1
                audit.log(
                    case.case_id,
                    "tool_rejected",
                    {
                        "tool": tc.name,
                        "arguments": tc.arguments,
                        "reason": decision.reason,
                        "correction_count": corrections,
                    },
                )
                if corrections > cap:
                    fallback_args = _fallback_message(case)
                    fallback_result = execute(
                        "send_message", fallback_args, notify_channel=notify_channel
                    )
                    audit.log(
                        case.case_id,
                        "gate_exhausted",
                        {
                            "fallback_action": "send_message",
                            "arguments": fallback_args,
                            "result": fallback_result,
                        },
                    )
                    actions.append(
                        {
                            "tool": "send_message",
                            "arguments": fallback_args,
                            "result": fallback_result,
                        }
                    )
                    return RunResult(
                        case_id=case.case_id,
                        actions=actions,
                        gate_exhausted=True,
                        correction_count=corrections,
                    )
                tool_result_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": _to_content({"error": decision.reason}),
                    }
                )

        messages.extend(tool_result_messages)

    return RunResult(
        case_id=case.case_id, actions=actions, gate_exhausted=False, correction_count=corrections
    )


def _to_content(obj: dict) -> str:
    import json

    return json.dumps(obj, ensure_ascii=False)
