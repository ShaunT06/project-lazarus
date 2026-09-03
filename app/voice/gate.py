"""The pre-dial gate: re-checked against live state before a call is placed
(and, per `_cleared_to_dial` in lazarusV2, before a ring is answered too -
a ring and a dial mean "permitted to call" the same way, so both go through
this). This is code, not a prompt - the same reasoning as app/policy_gate.py
for text, applied before any audio goes out instead of before a tool call.

Checks, in order:
1. Hard stop already active on the case -> refuse outright.
2. Customer has not opted in to outreach -> refuse (our consent proxy;
   lazarusV2 has a richer per-channel consent model, we don't have one).
3. Still inside the strategy's cooldown window -> refuse.
4. Outside quiet hours (default 09:00-21:00 local) -> refuse.
5. Contact budget for this case already spent -> refuse.
"""

from dataclasses import dataclass, field
from datetime import datetime

from app.models import CaseContext, StrategyResult
from app.voice.policy import DialoguePolicy


@dataclass
class GateResult:
    passed: bool
    blockers: list[str] = field(default_factory=list)
    reasons: dict[str, str] = field(default_factory=dict)


def pre_dial_gate(
    strategy: StrategyResult,
    case: CaseContext,
    policy: DialoguePolicy,
    *,
    now: datetime,
    call_attempts: int = 0,
    contacts_used: int = 0,
    max_contacts: int = 4,
) -> GateResult:
    blockers: list[str] = []
    reasons: dict[str, str] = {}

    if strategy.hard_stop:
        blockers.append("HARD_STOP_ACTIVE")
        reasons["HARD_STOP_ACTIVE"] = strategy.hard_stop_reason or "hard stop active"

    if not case.marketing_opt_in:
        blockers.append("NOT_OPTED_IN")
        reasons["NOT_OPTED_IN"] = "customer has not opted in to outreach"

    if case.hours_since_last_outreach < strategy.cooldown_hours:
        blockers.append("COOLDOWN_ACTIVE")
        reasons["COOLDOWN_ACTIVE"] = (
            f"{case.hours_since_last_outreach}h since last outreach, "
            f"needs >= {strategy.cooldown_hours}h"
        )

    start_hour, end_hour = policy.quiet_hours
    if not (start_hour <= now.hour < end_hour):
        blockers.append("OUTSIDE_CALL_WINDOW")
        reasons["OUTSIDE_CALL_WINDOW"] = (
            f"local hour {now.hour} is outside the {start_hour}:00-{end_hour}:00 call window"
        )

    if contacts_used >= max_contacts:
        blockers.append("CONTACT_BUDGET_EXHAUSTED")
        reasons["CONTACT_BUDGET_EXHAUSTED"] = (
            f"{contacts_used}/{max_contacts} contacts already used for this case"
        )

    return GateResult(passed=not blockers, blockers=blockers, reasons=reasons)
