"""Dialogue turn-engine tests use a scripted fake LLM client, same pattern
as tests/test_agent_gate_loop.py - no real OpenRouter calls in CI.
"""

import json
from datetime import datetime

from app.models import CaseContext, StrategyResult
from app.openrouter_client import LLMResponse, ToolCall
from app.voice.dialogue import Dialogue
from app.voice.policy import DialoguePolicy

NOW = datetime(2026, 9, 3, 12, 0)


class ScriptedClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def chat(self, messages, tools=None, **kwargs):
        self.calls.append(list(messages))
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
            "pitch": {
                "opening": "Hi, this is Lazarus calling on behalf of {merchant}.",
                "discount_fallback": "I can offer a discount within the approved amount.",
                "closing": "Goodbye.",
            },
            "levers": {"discount": "generate_payment_link"},
            "never_say": ["waive"],
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


def make_case() -> CaseContext:
    return CaseContext(case_id="c1", customer_id="u1", extra={"cause_category": "expired_card"})


def test_open_produces_opening_line_and_awaits_consent():
    d = Dialogue(make_policy(), make_strategy(), make_case(), NOW, merchant_name="Acme")
    turn = d.open()
    assert "Acme" in turn.text
    assert d.state == "awaiting_consent"


def test_declining_consent_closes_without_calling_llm():
    d = Dialogue(make_policy(), make_strategy(), make_case(), NOW)
    d.open()
    client = ScriptedClient([])
    d.respond("No, I don't want to talk about this.", client)
    assert d.consent is False
    assert d.state == "closed"
    assert d.outcome == "declined"
    assert client.calls == []  # never reached the LLM


def test_consenting_then_negotiating_calls_llm():
    d = Dialogue(make_policy(), make_strategy(), make_case(), NOW)
    d.open()
    client = ScriptedClient([make_response("Sure, I can help with that.")])
    turn = d.respond("Yes, go ahead.", client)
    assert d.consent is True
    assert d.state == "negotiating"
    assert turn.text == "Sure, I can help with that."


def test_record_commitment_closes_with_agreed_outcome():
    d = Dialogue(make_policy(), make_strategy(), make_case(), NOW)
    d.open()
    client = ScriptedClient(
        [
            make_response("Sure, I can help with that."),
            make_response(
                "Great, I'll get that set up.",
                "record_commitment",
                {"lever": "discount", "terms": {"discount_pct": 5}},
            ),
        ]
    )
    d.respond("Yes, go ahead.", client)
    turn = d.respond("Okay, 5% works for me.", client)
    assert d.state == "closed"
    assert d.outcome == "agreed"
    assert d.approved_discount_pct == 5
    assert turn.tool == {
        "name": "record_commitment",
        "arguments": {"lever": "discount", "terms": {"discount_pct": 5}},
    }


def test_overreaching_offer_is_blocked_and_replaced():
    d = Dialogue(make_policy(), make_strategy(max_discount_pct=5), make_case(), NOW)
    d.open()
    client = ScriptedClient([make_response("I can give you a 50% discount right now.")])
    turn = d.respond("Yes, go ahead.", client)
    assert turn.text != "I can give you a 50% discount right now."
    assert turn.meta["verifier"][0]["rule"] == "PCT_EXCEEDS_ENVELOPE"


def test_escalate_to_human_closes_with_escalated_outcome():
    d = Dialogue(make_policy(), make_strategy(), make_case(), NOW)
    d.open()
    client = ScriptedClient(
        [
            make_response("Sure, I can help with that."),
            make_response(
                "I'll have someone follow up with you.",
                "escalate_to_human",
                {"reason": "wants a manager"},
            ),
        ]
    )
    d.respond("Yes, go ahead.", client)
    d.respond("I want to speak to a manager.", client)
    assert d.state == "closed"
    assert d.outcome == "escalated"


def test_silence_nudges_then_closes():
    d = Dialogue(make_policy(), make_strategy(), make_case(), NOW)
    d.open()
    d.silence()
    assert d.state != "closed"
    d.silence()
    assert d.state == "closed"
    assert d.outcome == "silence"
