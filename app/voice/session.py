"""A live call: binds the Dialogue turn engine to the audit trail and to
app/tools.py's execute() - the same entry point the text agent uses, so
there is exactly one place that actually does anything, reached from both
the text lane and the voice lane.

This is also where "voice negotiates, text commits" is enforced:
record_commitment only records terms; end() reports outcome "agreed" only
if the paired confirmation message actually passed app/policy_gate.py and
was sent. If it wasn't (the gate rejected it, or something raised), the
outcome is downgraded to "agreed_unconfirmed" and an audit.alert is logged
- nothing spoken on a call was ever binding on its own.

Sessions live in memory only, for the call's duration - a process restart
mid-call should abandon the call, not resume a stale negotiation with a
customer who may have already hung up. The durable record is the audit
log (and, once wired, app/voice_store.py).
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.audit import AuditLogger
from app.models import CaseContext, StrategyResult
from app.policy_gate import validate
from app.tools import execute
from app.voice.dialogue import Dialogue, Turn
from app.voice.gate import pre_dial_gate
from app.voice.policy import DialoguePolicy

_LIVE: dict[str, "CallSession"] = {}


class GateRefused(Exception):
    def __init__(self, blockers: list[str], reasons: dict[str, str]):
        super().__init__("; ".join(blockers))
        self.blockers = blockers
        self.reasons = reasons


@dataclass
class CallSession:
    call_id: str
    case_id: str
    conversation_id: str
    case: CaseContext
    dialogue: Dialogue
    started_at: datetime
    ended: bool = False
    committed: bool = False
    transcript: list[dict[str, Any]] = field(default_factory=list)
    reconciliation: dict[str, Any] | None = None

    def public(self) -> dict[str, Any]:
        d = self.dialogue
        return {
            "call_id": self.call_id,
            "case_id": self.case_id,
            "state": d.state,
            "consent": d.consent,
            "outcome": d.outcome,
            "ended": self.ended,
            "committed": self.committed,
            "transcript": self.transcript,
            "envelope": {
                "max_discount_pct": d.strategy.max_discount_pct,
                "allowed_actions": d.strategy.allowed_actions,
                "approved_discount_pct": d.approved_discount_pct,
            },
            "reconciliation": self.reconciliation,
        }


def get_session(call_id: str) -> CallSession | None:
    return _LIVE.get(call_id)


def _append_transcript(
    session: CallSession, role: str, text: str, *, meta: dict[str, Any] | None = None
) -> None:
    session.transcript.append(
        {"role": role, "text": text, "ts": datetime.now(UTC).isoformat(), "meta": meta or {}}
    )


def place(
    case: CaseContext,
    strategy: StrategyResult,
    policy: DialoguePolicy,
    audit: AuditLogger,
    *,
    now: datetime | None = None,
    call_attempts: int = 0,
    contacts_used: int = 0,
    max_contacts: int = 4,
    merchant_name: str = "your merchant",
) -> tuple[CallSession, Turn]:
    now = now or datetime.now()
    gate = pre_dial_gate(
        strategy,
        case,
        policy,
        now=now,
        call_attempts=call_attempts,
        contacts_used=contacts_used,
        max_contacts=max_contacts,
    )
    if not gate.passed:
        audit.log(
            case.case_id,
            "call.refused",
            {"blockers": gate.blockers, "reasons": gate.reasons},
        )
        raise GateRefused(gate.blockers, gate.reasons)

    call_id = f"call_{uuid.uuid4().hex[:10]}"
    conversation_id = f"conv_{uuid.uuid4().hex[:10]}"
    dialogue = Dialogue(policy, strategy, case, now, merchant_name=merchant_name)
    session = CallSession(
        call_id=call_id,
        case_id=case.case_id,
        conversation_id=conversation_id,
        case=case,
        dialogue=dialogue,
        started_at=now,
    )
    _LIVE[call_id] = session

    opening = dialogue.open()
    _append_transcript(session, "agent", opening.text)
    audit.log(
        case.case_id,
        "call.placed",
        {
            "call_id": call_id,
            "conversation_id": conversation_id,
            "dialogue_policy_id": policy.id,
            "gate_reasons": gate.reasons,
        },
    )
    return session, opening


def _describe_terms(lever: str | None, terms: dict[str, Any]) -> str:
    if lever == "discount":
        return f"a {terms.get('discount_pct', 0)}% discount on your next payment"
    if lever == "split":
        return f"a split payment over {terms.get('installments', 2)} installments"
    if lever == "retry":
        return f"we'll retry the payment in {terms.get('delay_hours', 24)} hours"
    return "what we discussed on the call"


def _confirm_commitment(
    session: CallSession,
    commitment_args: dict[str, Any],
    audit: AuditLogger,
    *,
    notify_channel: str,
) -> None:
    lever = commitment_args.get("lever")
    terms = commitment_args.get("terms", {}) or {}
    body = f"Confirming what we agreed on the call: {_describe_terms(lever, terms)}."

    decision = validate(
        "send_message",
        {"body": body},
        session.dialogue.strategy,
        session.case,
        approved_discount_pct_this_run=session.dialogue.approved_discount_pct,
    )
    if not decision.approved:
        audit.log(
            session.case_id,
            "audit.alert",
            {
                "call_id": session.call_id,
                "reason": "commitment confirmation was rejected by the policy gate",
                "gate_reason": decision.reason,
            },
        )
        return

    result = execute("send_message", {"body": body}, notify_channel=notify_channel)
    audit.log(
        session.case_id,
        "action.completed",
        {
            "call_id": session.call_id,
            "tool": "send_message",
            "arguments": {"body": body},
            "result": result,
        },
    )
    session.committed = True


def _run_tool(session: CallSession, turn: Turn, audit: AuditLogger, *, notify_channel: str) -> None:
    assert turn.tool is not None
    name, args = turn.tool["name"], turn.tool["arguments"]
    result = execute(name, args, notify_channel=notify_channel)
    audit.log(
        session.case_id,
        "action.completed",
        {"call_id": session.call_id, "tool": name, "arguments": args, "result": result},
    )
    if name == "record_commitment":
        _confirm_commitment(session, args, audit, notify_channel=notify_channel)


def say(
    session: CallSession,
    said: str,
    audit: AuditLogger,
    *,
    chat_client: Any,
    notify_channel: str = "console",
) -> Turn:
    if session.ended:
        raise ValueError("call has ended")

    _append_transcript(session, "customer", said)
    audit.log(
        session.case_id,
        "call.turn",
        {"call_id": session.call_id, "role": "customer", "text": said},
    )

    turn = session.dialogue.respond(said, chat_client)
    _append_transcript(session, "agent", turn.text, meta=turn.meta)
    audit.log(
        session.case_id,
        "call.turn",
        {"call_id": session.call_id, "role": "agent", "text": turn.text},
    )

    if turn.meta.get("verifier"):
        audit.log(
            session.case_id,
            "voice.utterance_blocked",
            {
                "call_id": session.call_id,
                "findings": turn.meta["verifier"],
                "wanted_to_say": turn.meta.get("blocked_text"),
                "spoken_instead": turn.text,
            },
        )

    if turn.tool:
        _run_tool(session, turn, audit, notify_channel=notify_channel)

    if session.dialogue.state == "closed":
        end(session, audit, session.dialogue.outcome or "ended")

    return turn


def silence(session: CallSession, audit: AuditLogger) -> Turn:
    if session.ended:
        return Turn(role="agent", text="")
    turn = session.dialogue.silence()
    _append_transcript(session, "agent", turn.text)
    audit.log(
        session.case_id,
        "call.silence",
        {"call_id": session.call_id, "count": session.dialogue.silences},
    )
    if session.dialogue.state == "closed":
        end(session, audit, session.dialogue.outcome or "silence")
    return turn


def end(session: CallSession, audit: AuditLogger, outcome: str) -> None:
    if session.ended:
        return
    session.ended = True
    if outcome == "agreed" and not session.committed:
        outcome = "agreed_unconfirmed"
        audit.log(
            session.case_id,
            "audit.alert",
            {
                "call_id": session.call_id,
                "reason": "call ended agreed but the confirmation message was never sent",
            },
        )
    session.dialogue.outcome = outcome
    audit.log(
        session.case_id,
        "call.ended",
        {"call_id": session.call_id, "outcome": outcome, "committed": session.committed},
    )
    # Deliberately not popped from _LIVE here - reconcile.run() and the UI's
    # final poll both still need get_session() to work after end().
