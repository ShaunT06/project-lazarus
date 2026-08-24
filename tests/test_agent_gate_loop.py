"""Agent loop tests use a scripted fake LLM client - no real OpenRouter calls
in CI. Exercises the part that matters most for the demo: the agent
proposing out-of-bounds actions repeatedly and the gate stopping it safely.
"""

import json
from pathlib import Path

import pytest

from app.agent import run_case
from app.audit import AuditLogger
from app.models import CaseContext
from app.openrouter_client import LLMResponse, ToolCall
from app.strategy import StrategyEngine


class ScriptedClient:
    """Returns queued responses in order; ignores the actual messages sent."""

    def __init__(self, responses: list[LLMResponse]):
        self._responses = list(responses)

    def chat(self, messages, tools=None):
        return self._responses.pop(0)


def make_tool_call(name: str, arguments: dict) -> LLMResponse:
    return LLMResponse(
        content=None,
        tool_calls=[ToolCall(id="call_1", name=name, arguments=arguments)],
        raw_message={
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(arguments)},
                }
            ],
        },
    )


@pytest.fixture
def strategy():
    engine = StrategyEngine.from_file(Path("config/strategy.example.json"))
    case = CaseContext(case_id="c1", customer_id="u1", extra={"cause_category": "expired_card"})
    return engine.evaluate(case), case


def test_repeated_out_of_bounds_calls_hit_gate_exhausted(strategy, tmp_path):
    result, case = strategy
    # card_problem rule: allowed_actions = [send_message, generate_payment_link],
    # max_discount_pct = 0. Script the agent proposing a 50% discount 5 times
    # in a row (never correcting) to force gate_exhausted.
    responses = [make_tool_call("generate_payment_link", {"discount_pct": 50}) for _ in range(6)]
    client = ScriptedClient(responses)
    audit = AuditLogger(tmp_path / "audit.jsonl")

    run_result = run_case(
        case, "expired_card", result, client, audit, max_corrections=3, notify_channel="console"
    )

    assert run_result.gate_exhausted is True
    assert run_result.correction_count == 4  # cap(3) exceeded on the 4th rejection
    assert run_result.actions[-1]["tool"] == "send_message"

    log_lines = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").strip().splitlines()
    events = [json.loads(line)["event_type"] for line in log_lines]
    assert "gate_exhausted" in events
    assert events.count("tool_rejected") == 4


def test_approved_call_executes_and_stops(strategy, tmp_path):
    result, case = strategy
    responses = [
        make_tool_call("send_message", {"body": "We noticed an issue with your payment."}),
        LLMResponse(
            content="Done.", tool_calls=[], raw_message={"role": "assistant", "content": "Done."}
        ),
    ]
    client = ScriptedClient(responses)
    audit = AuditLogger(tmp_path / "audit.jsonl")

    run_result = run_case(case, "expired_card", result, client, audit, notify_channel="console")

    assert run_result.gate_exhausted is False
    assert len(run_result.actions) == 1
    assert run_result.actions[0]["tool"] == "send_message"


def test_hard_stop_takes_no_action(tmp_path):
    engine = StrategyEngine.from_file(Path("config/strategy.example.json"))
    case = CaseContext(case_id="c2", customer_id="u2", abandons_last_7d=3)
    result = engine.evaluate(case)
    audit = AuditLogger(tmp_path / "audit.jsonl")

    run_result = run_case(case, "unknown", result, client=ScriptedClient([]), audit=audit)

    assert run_result.actions == []
    assert run_result.gate_exhausted is False
