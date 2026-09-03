from datetime import datetime

from app.models import CaseContext, StrategyResult
from app.voice.gate import pre_dial_gate
from app.voice.policy import DialoguePolicy


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


def make_policy(**quiet_hours) -> DialoguePolicy:
    raw = {"quiet_hours": {"start_hour": 9, "end_hour": 21}}
    raw["quiet_hours"].update(quiet_hours)
    return DialoguePolicy(raw)


NOON = datetime(2026, 9, 3, 12, 0)


def test_clean_case_passes():
    gate = pre_dial_gate(make_strategy(), make_case(), make_policy(), now=NOON)
    assert gate.passed is True
    assert gate.blockers == []


def test_hard_stop_blocks():
    strategy = make_strategy(hard_stop=True, hard_stop_reason="third abandonment this week")
    gate = pre_dial_gate(strategy, make_case(), make_policy(), now=NOON)
    assert gate.passed is False
    assert "HARD_STOP_ACTIVE" in gate.blockers
    assert gate.reasons["HARD_STOP_ACTIVE"] == "third abandonment this week"


def test_not_opted_in_blocks():
    gate = pre_dial_gate(
        make_strategy(), make_case(marketing_opt_in=False), make_policy(), now=NOON
    )
    assert gate.passed is False
    assert "NOT_OPTED_IN" in gate.blockers


def test_cooldown_blocks():
    strategy = make_strategy(cooldown_hours=48)
    case = make_case(hours_since_last_outreach=2)
    gate = pre_dial_gate(strategy, case, make_policy(), now=NOON)
    assert gate.passed is False
    assert "COOLDOWN_ACTIVE" in gate.blockers


def test_outside_quiet_hours_blocks():
    late_night = datetime(2026, 9, 3, 23, 0)
    gate = pre_dial_gate(make_strategy(), make_case(), make_policy(), now=late_night)
    assert gate.passed is False
    assert "OUTSIDE_CALL_WINDOW" in gate.blockers


def test_contact_budget_exhausted_blocks():
    gate = pre_dial_gate(
        make_strategy(), make_case(), make_policy(), now=NOON, contacts_used=4, max_contacts=4
    )
    assert gate.passed is False
    assert "CONTACT_BUDGET_EXHAUSTED" in gate.blockers


def test_multiple_blockers_all_reported():
    strategy = make_strategy(hard_stop=True, hard_stop_reason="x")
    case = make_case(marketing_opt_in=False)
    gate = pre_dial_gate(strategy, case, make_policy(), now=NOON)
    assert set(gate.blockers) == {"HARD_STOP_ACTIVE", "NOT_OPTED_IN"}
