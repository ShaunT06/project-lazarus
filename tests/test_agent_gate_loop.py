"""Agent loop tests use a scripted fake LLM client - no real OpenRouter calls
in CI. Exercises the part that matters most for the demo: the agent
proposing out-of-bounds actions repeatedly and the gate stopping it safely.
"""

import json
from pathlib import Path

import pytest

import app.agent as agent_module
from app.agent import continue_conversation, run_case, start_conversation
from app.audit import AuditLogger
from app.models import CaseContext
from app.openrouter_client import LLMResponse, ToolCall
from app.strategy import StrategyEngine


class ScriptedClient:
    """Returns queued responses in order; records the messages it was called
    with so tests can assert on what got sent back (e.g. a nudge)."""

    def __init__(self, responses: list[LLMResponse]):
        self._responses = list(responses)
        self.calls: list[list[dict]] = []

    def chat(self, messages, tools=None, **kwargs):
        self.calls.append(list(messages))
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


def test_no_tool_call_on_first_turn_gets_nudged_then_acts(strategy, tmp_path):
    # Reproduces the observed free-model failure mode: the first turn
    # reasons in plain text without calling a tool. That should NOT be
    # accepted as "no action needed" immediately - it should be nudged once.
    responses = [
        LLMResponse(
            content="I need to handle this case...",
            tool_calls=[],
            raw_message={"role": "assistant", "content": "I need to handle this case..."},
        ),
        make_tool_call("send_message", {"body": "Your card has expired, please update it."}),
        LLMResponse(
            content="Done.", tool_calls=[], raw_message={"role": "assistant", "content": "Done."}
        ),
    ]
    result, case = strategy
    client = ScriptedClient(responses)
    audit = AuditLogger(tmp_path / "audit.jsonl")

    run_result = run_case(case, "expired_card", result, client, audit, notify_channel="console")

    assert len(run_result.actions) == 1
    assert run_result.actions[0]["tool"] == "send_message"
    assert len(client.calls) == 3  # first (no tool), nudge retry, final "done" turn
    nudge_message = client.calls[1][-1]
    assert nudge_message["role"] == "user"
    assert "did not call a tool" in nudge_message["content"]

    log_lines = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").strip().splitlines()
    events = [json.loads(line)["event_type"] for line in log_lines]
    assert "no_tool_call_nudged" in events


def test_no_tool_call_twice_in_a_row_is_accepted_as_no_action(strategy, tmp_path):
    # The nudge is capped at one retry - if the model still doesn't call a
    # tool after being nudged, that's accepted as genuine "no action" rather
    # than nudging forever.
    responses = [
        LLMResponse(
            content="Thinking...",
            tool_calls=[],
            raw_message={"role": "assistant", "content": "Thinking..."},
        ),
        LLMResponse(
            content="Still thinking...",
            tool_calls=[],
            raw_message={"role": "assistant", "content": "Still thinking..."},
        ),
    ]
    result, case = strategy
    client = ScriptedClient(responses)
    audit = AuditLogger(tmp_path / "audit.jsonl")

    run_result = run_case(case, "expired_card", result, client, audit, notify_channel="console")

    assert run_result.actions == []
    assert len(client.calls) == 2  # exactly one nudge attempt, then accepted


def test_no_tool_call_after_an_action_is_accepted_immediately(strategy, tmp_path):
    # A no-tool-call turn AFTER a real action has already been taken is
    # genuine completion, not the confusion failure mode - must not nudge.
    responses = [
        make_tool_call("send_message", {"body": "Your card has expired, please update it."}),
        LLMResponse(
            content="Done.", tool_calls=[], raw_message={"role": "assistant", "content": "Done."}
        ),
    ]
    result, case = strategy
    client = ScriptedClient(responses)
    audit = AuditLogger(tmp_path / "audit.jsonl")

    run_result = run_case(case, "expired_card", result, client, audit, notify_channel="console")

    assert len(run_result.actions) == 1
    assert len(client.calls) == 2  # no nudge inserted after a real action


def test_hard_stop_takes_no_action(tmp_path):
    engine = StrategyEngine.from_file(Path("config/strategy.example.json"))
    case = CaseContext(case_id="c2", customer_id="u2", abandons_last_7d=3)
    result = engine.evaluate(case)
    audit = AuditLogger(tmp_path / "audit.jsonl")

    run_result = run_case(case, "unknown", result, client=ScriptedClient([]), audit=audit)

    assert run_result.actions == []
    assert run_result.gate_exhausted is False


def test_chat_stops_at_expired_deadline_before_any_llm_call(strategy, tmp_path, monkeypatch):
    # Reproduces the real bug: a live chat request sits behind Vercel's 60s
    # hard function timeout, which kills the whole process before our own
    # error handling runs. If the deadline has already passed, we must not
    # call the LLM at all - not even once - and still return a clean result.
    monkeypatch.setattr(agent_module, "CHAT_TURN_DEADLINE_SECONDS", -1.0)
    result, case = strategy
    client = ScriptedClient([])  # would raise IndexError if ever called
    audit = AuditLogger(tmp_path / "audit.jsonl")

    outcome = start_conversation(
        case, "expired_card", result, client, audit, notify_channel="console"
    )

    assert outcome["actions"] == []
    assert outcome["hard_stop"] is False
    assert len(client.calls) == 0

    log_lines = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").strip().splitlines()
    events = [json.loads(line)["event_type"] for line in log_lines]
    assert "time_budget_exhausted" in events


def test_chat_completes_normally_within_deadline(strategy, tmp_path):
    result, case = strategy
    responses = [
        make_tool_call("send_message", {"body": "Your card has expired, please update it."}),
        LLMResponse(
            content="Done.", tool_calls=[], raw_message={"role": "assistant", "content": "Done."}
        ),
    ]
    client = ScriptedClient(responses)
    audit = AuditLogger(tmp_path / "audit.jsonl")

    outcome = start_conversation(
        case, "expired_card", result, client, audit, notify_channel="console"
    )

    assert len(outcome["actions"]) == 1
    assert outcome["actions"][0]["tool"] == "send_message"


def test_chat_stops_as_soon_as_the_customer_has_a_reply(strategy, tmp_path):
    # The latency fix: once an approved send_message has gone out and
    # nothing was rejected, the chat path must not spend another LLM round
    # trip confirming the model is finished. That trailing turn's text is
    # never rendered (the UI builds bubbles from `actions`), so it was pure
    # wall-clock cost in front of a waiting customer.
    result, case = strategy
    responses = [
        make_tool_call("send_message", {"body": "Your card has expired, please update it."}),
        LLMResponse(
            content="Done.", tool_calls=[], raw_message={"role": "assistant", "content": "Done."}
        ),
    ]
    client = ScriptedClient(responses)
    audit = AuditLogger(tmp_path / "audit.jsonl")

    outcome = start_conversation(
        case, "expired_card", result, client, audit, notify_channel="console"
    )

    assert len(client.calls) == 1  # the confirmation turn is gone
    assert [a["tool"] for a in outcome["actions"]] == ["send_message"]

    log_lines = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").strip().splitlines()
    events = [json.loads(line)["event_type"] for line in log_lines]
    assert "stopped_after_reply" in events


def test_chat_still_takes_another_turn_when_the_gate_rejected_something(strategy, tmp_path):
    # Stopping early must never swallow a correction: if the gate rejected a
    # call in the same turn as the reply, the model has to see the rejection
    # and get its chance to correct. Latency does not outrank the fence.
    result, case = strategy
    turn_one = LLMResponse(
        content=None,
        tool_calls=[
            ToolCall(id="call_1", name="send_message", arguments={"body": "Sorted - here you go."}),
            ToolCall(id="call_2", name="generate_payment_link", arguments={"discount_pct": 50}),
        ],
        raw_message={
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "send_message",
                        "arguments": json.dumps({"body": "Sorted - here you go."}),
                    },
                },
                {
                    "id": "call_2",
                    "type": "function",
                    "function": {
                        "name": "generate_payment_link",
                        "arguments": json.dumps({"discount_pct": 50}),
                    },
                },
            ],
        },
    )
    responses = [
        turn_one,
        make_tool_call("send_message", {"body": "No discount is available on this one."}),
    ]
    client = ScriptedClient(responses)
    audit = AuditLogger(tmp_path / "audit.jsonl")

    outcome = start_conversation(
        case, "expired_card", result, client, audit, notify_channel="console"
    )

    assert len(client.calls) == 2  # did not stop on the rejected turn
    assert [a["tool"] for a in outcome["actions"]] == ["send_message", "send_message"]
    assert len(outcome["rejections"]) == 1


def test_chat_keeps_going_when_a_turn_produced_only_a_link(strategy, tmp_path):
    # A payment link with no words is not a reply - the customer would see a
    # bare button and no explanation, so the loop must continue until the
    # agent actually says something.
    result, case = strategy
    responses = [
        make_tool_call("generate_payment_link", {"discount_pct": 0}),
        make_tool_call("send_message", {"body": "Here is a fresh link to update your card."}),
    ]
    client = ScriptedClient(responses)
    audit = AuditLogger(tmp_path / "audit.jsonl")

    outcome = start_conversation(
        case, "expired_card", result, client, audit, notify_channel="console"
    )

    assert len(client.calls) == 2
    assert [a["tool"] for a in outcome["actions"]] == ["generate_payment_link", "send_message"]


def test_continue_conversation_stops_after_a_clean_reply(strategy, tmp_path):
    result, case = strategy
    responses = [
        make_tool_call("send_message", {"body": "I can't go beyond what's approved here."}),
        LLMResponse(
            content="Done.", tool_calls=[], raw_message={"role": "assistant", "content": "Done."}
        ),
    ]
    client = ScriptedClient(responses)
    audit = AuditLogger(tmp_path / "audit.jsonl")

    outcome = continue_conversation(
        case=case,
        strategy=result,
        client=client,
        audit=audit,
        messages=[{"role": "system", "content": "..."}],
        corrections=0,
        approved_discount_pct=0.0,
        customer_message="can I get a bigger discount?",
        notify_channel="console",
    )

    assert len(client.calls) == 1
    assert [a["tool"] for a in outcome["actions"]] == ["send_message"]


def test_batch_path_keeps_the_confirmation_turn(strategy, tmp_path):
    # run_case (webhook/batch) is deliberately NOT changed - no human is
    # waiting on it, and the published recovery numbers were measured with
    # the extra turn in place.
    result, case = strategy
    responses = [
        make_tool_call("send_message", {"body": "Your card has expired, please update it."}),
        LLMResponse(
            content="Done.", tool_calls=[], raw_message={"role": "assistant", "content": "Done."}
        ),
    ]
    client = ScriptedClient(responses)
    audit = AuditLogger(tmp_path / "audit.jsonl")

    run_case(case, "expired_card", result, client, audit, notify_channel="console")

    assert len(client.calls) == 2


def test_continue_conversation_also_stops_at_expired_deadline(strategy, tmp_path, monkeypatch):
    monkeypatch.setattr(agent_module, "CHAT_TURN_DEADLINE_SECONDS", -1.0)
    result, case = strategy
    client = ScriptedClient([])
    audit = AuditLogger(tmp_path / "audit.jsonl")

    outcome = continue_conversation(
        case=case,
        strategy=result,
        client=client,
        audit=audit,
        messages=[{"role": "system", "content": "..."}],
        corrections=0,
        approved_discount_pct=0.0,
        customer_message="can I get a bigger discount?",
        notify_channel="console",
    )

    assert outcome["actions"] == []
    assert len(client.calls) == 0
