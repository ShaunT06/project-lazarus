import json
from datetime import datetime
from pathlib import Path

import pytest

from app.audit import AuditLogger
from app.models import CaseContext, StrategyResult
from app.openrouter_client import LLMResponse, ToolCall
from app.voice import session as voice_session
from app.voice.policy import DialoguePolicy

NOON = datetime(2026, 9, 3, 12, 0)


class ScriptedClient:
    def __init__(self, responses):
        self._responses = list(responses)

    def chat(self, messages, tools=None, **kwargs):
        return self._responses.pop(0)


def _raw_call(call_id: str, name: str, arguments: dict) -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


def make_response(speak_text, tool_name=None, tool_args=None):
    tool_calls = [ToolCall(id="c1", name="speak", arguments={"text": speak_text})]
    raw_tool_calls = [_raw_call("c1", "speak", {"text": speak_text})]
    if tool_name:
        tool_calls.append(ToolCall(id="c2", name=tool_name, arguments=tool_args or {}))
        raw_tool_calls.append(_raw_call("c2", tool_name, tool_args or {}))
    return LLMResponse(
        content=None,
        tool_calls=tool_calls,
        raw_message={"role": "assistant", "tool_calls": raw_tool_calls},
    )


def make_policy() -> DialoguePolicy:
    return DialoguePolicy(
        {
            "quiet_hours": {"start_hour": 9, "end_hour": 21},
            "pitch": {"opening": "Hi, this is Lazarus calling.", "closing": "Goodbye."},
            "levers": {"discount": "generate_payment_link"},
        }
    )


def make_strategy(**overrides) -> StrategyResult:
    base = dict(
        matched_rule_id="test_rule",
        allowed_actions=["send_message", "generate_payment_link"],
        max_discount_pct=5,
        max_retries=2,
        cooldown_hours=24,
    )
    base.update(overrides)
    return StrategyResult(**base)


def make_case(**overrides) -> CaseContext:
    base = dict(
        case_id="c1", customer_id="u1", marketing_opt_in=True, hours_since_last_outreach=999
    )
    base.update(overrides)
    return CaseContext(**base)


@pytest.fixture
def audit(tmp_path: Path) -> AuditLogger:
    return AuditLogger(tmp_path / "audit.jsonl")


def test_gate_refused_raises(audit):
    strategy = make_strategy(hard_stop=True, hard_stop_reason="stop")
    with pytest.raises(voice_session.GateRefused):
        voice_session.place(make_case(), strategy, make_policy(), audit, now=NOON)


def test_place_opens_call_and_records_audit(audit):
    call_session, opening = voice_session.place(
        make_case(), make_strategy(), make_policy(), audit, now=NOON
    )
    assert opening.text == "Hi, this is Lazarus calling."
    assert call_session.transcript[0]["role"] == "agent"
    events = [r["event_type"] for r in audit.read(call_session.case_id)]
    assert "call.placed" in events


def test_record_commitment_sends_confirmation_and_marks_committed(audit):
    call_session, _ = voice_session.place(
        make_case(), make_strategy(), make_policy(), audit, now=NOON
    )
    client = ScriptedClient(
        [
            make_response("Sure, I can help."),
            make_response(
                "I'll text you a confirmation.",
                "record_commitment",
                {"lever": "discount", "terms": {"discount_pct": 5}},
            ),
        ]
    )
    voice_session.say(call_session, "Yes, go ahead.", audit, chat_client=client)
    voice_session.say(call_session, "5% works.", audit, chat_client=client)

    assert call_session.ended is True
    assert call_session.committed is True
    assert call_session.dialogue.outcome == "agreed"
    events = [r["event_type"] for r in audit.read(call_session.case_id)]
    assert "action.completed" in events
    assert "call.ended" in events
    assert "audit.alert" not in events


def test_confirmation_rejected_by_gate_downgrades_outcome(audit):
    """An overreaching commitment (discount beyond the strategy's cap) means
    the confirmation message itself gets caught by app/policy_gate.py - the
    call must not report "agreed" when nothing was actually confirmed."""
    call_session, _ = voice_session.place(
        make_case(), make_strategy(max_discount_pct=0), make_policy(), audit, now=NOON
    )
    client = ScriptedClient(
        [
            make_response("Sure, I can help."),
            make_response(
                "Confirming your discount.",
                "record_commitment",
                # verify.py wouldn't catch this (no % in the spoken text), but
                # the commitment's own terms exceed the strategy bound - the
                # confirmation message text itself will name the % and the
                # existing text-lane gate (policy_gate.validate) must catch it.
                {"lever": "discount", "terms": {"discount_pct": 5}},
            ),
        ]
    )
    voice_session.say(call_session, "Yes, go ahead.", audit, chat_client=client)
    voice_session.say(call_session, "Okay.", audit, chat_client=client)

    assert call_session.committed is False
    assert call_session.dialogue.outcome == "agreed_unconfirmed"
    events = [r["event_type"] for r in audit.read(call_session.case_id)]
    assert "audit.alert" in events


def test_silence_ends_call_after_repeated_nudges(audit):
    call_session, _ = voice_session.place(
        make_case(), make_strategy(), make_policy(), audit, now=NOON
    )
    voice_session.silence(call_session, audit)
    assert call_session.ended is False
    voice_session.silence(call_session, audit)
    assert call_session.ended is True
    assert call_session.dialogue.outcome == "silence"
