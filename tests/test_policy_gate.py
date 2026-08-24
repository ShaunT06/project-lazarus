from app.models import CaseContext, StrategyResult
from app.policy_gate import validate


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
    base = dict(case_id="c1", customer_id="u1", hours_since_last_outreach=999)
    base.update(overrides)
    return CaseContext(**base)


def test_disallowed_tool_rejected():
    decision = validate("schedule_retry", {}, make_strategy(), make_case())
    assert decision.approved is False
    assert "not in allowed_actions" in decision.reason


def test_discount_within_cap_approved():
    decision = validate("generate_payment_link", {"discount_pct": 5}, make_strategy(), make_case())
    assert decision.approved is True


def test_discount_over_cap_rejected():
    decision = validate("generate_payment_link", {"discount_pct": 20}, make_strategy(), make_case())
    assert decision.approved is False
    assert "exceeds max_discount_pct" in decision.reason


def test_cooldown_active_rejects_outreach():
    decision = validate(
        "send_message", {"body": "hello"}, make_strategy(), make_case(hours_since_last_outreach=1)
    )
    assert decision.approved is False
    assert "cooldown active" in decision.reason


def test_hard_stop_rejects_everything():
    strategy = make_strategy(hard_stop=True, hard_stop_reason="opted out")
    decision = validate("send_message", {"body": "hi"}, strategy, make_case())
    assert decision.approved is False
    assert decision.reason == "opted out"


def test_message_body_claiming_unapproved_discount_is_rejected():
    decision = validate(
        "send_message",
        {"body": "Use this link for 50% off your order!"},
        make_strategy(max_discount_pct=5),
        make_case(),
    )
    assert decision.approved is False
    assert "references 50.0%" in decision.reason


def test_message_body_within_approved_discount_is_allowed():
    decision = validate(
        "send_message",
        {"body": "Here's your link with 5% off."},
        make_strategy(max_discount_pct=5),
        make_case(),
    )
    assert decision.approved is True
