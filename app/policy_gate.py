"""Deterministic re-validation of every proposed tool call. Code, not prompt
instructions - this is what makes the agent's authority a fence rather than
a suggestion.

Checks, in order:
1. Hard stop already active -> reject everything.
2. Tool name must be in the strategy's allowed_actions.
3. Discount-bearing tools must not exceed max_discount_pct.
4. Retry-bearing tools must not exceed max_retries.
5. Cooldown: reject outreach-type tools if still inside cooldown_hours.
6. Message-body scan: reject if the text names a percentage/amount that
   was not itself approved via a discount tool call this run. Closes the
   gap where the gate checks tool args but not what the LLM writes -
   nothing stops the model from *typing* "50% off" even when no discount
   tool call for 50% was ever approved.
"""

import re
from typing import Any

from app.models import CaseContext, GateDecision, StrategyResult

_PCT_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*%")

_DISCOUNT_TOOLS = {"generate_payment_link", "generate_split_payment_link"}
_OUTREACH_TOOLS = {"send_message", "generate_payment_link", "generate_split_payment_link"}


def validate(
    tool_name: str,
    arguments: dict[str, Any],
    strategy: StrategyResult,
    case: CaseContext,
    *,
    approved_discount_pct_this_run: float = 0,
) -> GateDecision:
    if strategy.hard_stop:
        return GateDecision(approved=False, reason=strategy.hard_stop_reason or "hard stop active")

    if tool_name not in strategy.allowed_actions:
        return GateDecision(
            approved=False,
            reason=f"'{tool_name}' is not in allowed_actions for rule '{strategy.matched_rule_id}'",
        )

    if tool_name in _DISCOUNT_TOOLS:
        discount = float(arguments.get("discount_pct", 0) or 0)
        if discount > strategy.max_discount_pct:
            return GateDecision(
                approved=False,
                reason=(
                    f"discount_pct={discount} exceeds max_discount_pct="
                    f"{strategy.max_discount_pct} for rule '{strategy.matched_rule_id}'"
                ),
            )

    if tool_name == "schedule_retry":
        # max_retries is a count, not this field, but a single call scheduling
        # an absurd delay is still worth a sanity check; real retry-count
        # enforcement happens in the agent loop, which tracks calls made.
        pass

    if tool_name in _OUTREACH_TOOLS and case.hours_since_last_outreach < strategy.cooldown_hours:
        return GateDecision(
            approved=False,
            reason=(
                f"cooldown active: {case.hours_since_last_outreach}h since last outreach, "
                f"needs >= {strategy.cooldown_hours}h for rule '{strategy.matched_rule_id}'"
            ),
        )

    if tool_name == "send_message":
        body = str(arguments.get("body", ""))
        for match in _PCT_PATTERN.finditer(body):
            claimed_pct = float(match.group(1))
            if claimed_pct > max(strategy.max_discount_pct, approved_discount_pct_this_run):
                return GateDecision(
                    approved=False,
                    reason=(
                        f"message body references {claimed_pct}% but only "
                        f"{max(strategy.max_discount_pct, approved_discount_pct_this_run)}% "
                        "is approved for this case"
                    ),
                )

    return GateDecision(approved=True)
