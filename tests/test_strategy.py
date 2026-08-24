from pathlib import Path

import pytest

from app.models import CaseContext
from app.strategy import StrategyEngine

CONFIG_PATH = Path("config/strategy.example.json")


@pytest.fixture
def engine() -> StrategyEngine:
    return StrategyEngine.from_file(CONFIG_PATH)


def test_hard_stop_on_abandon_fatigue(engine: StrategyEngine):
    case = CaseContext(case_id="c1", customer_id="u1", abandons_last_7d=3)
    result = engine.evaluate(case)
    assert result.hard_stop is True
    assert result.allowed_actions == []


def test_high_ltv_insufficient_funds_gets_discount_room(engine: StrategyEngine):
    case = CaseContext(
        case_id="c2",
        customer_id="u2",
        customer_ltv_inr=30000,
        extra={"cause_category": "insufficient_funds"},
    )
    result = engine.evaluate(case)
    assert result.matched_rule_id == "high_ltv_insufficient_funds"
    assert result.max_discount_pct == 5
    assert "generate_payment_link" in result.allowed_actions


def test_card_problem_gets_no_discount_no_retry(engine: StrategyEngine):
    case = CaseContext(case_id="c3", customer_id="u3", extra={"cause_category": "expired_card"})
    result = engine.evaluate(case)
    assert result.matched_rule_id == "card_problem"
    assert result.max_discount_pct == 0
    assert result.max_retries == 0


def test_unmatched_case_falls_back_to_defaults(engine: StrategyEngine):
    case = CaseContext(case_id="c4", customer_id="u4", extra={"cause_category": "totally_novel"})
    result = engine.evaluate(case)
    assert result.matched_rule_id == "__default__"
    assert result.allowed_actions == ["send_message"]


def test_first_time_checkout_abandonment_gets_discount_room(engine: StrategyEngine):
    case = CaseContext(
        case_id="c5",
        customer_id="u5",
        category="checkout_abandonment",
        abandons_last_7d=1,
        extra={"cause_category": "abandoned_checkout"},
    )
    result = engine.evaluate(case)
    assert result.matched_rule_id == "checkout_abandonment_first_time"
    assert result.max_discount_pct == 3


def test_receivable_gets_split_payment_not_checkout_rule(engine: StrategyEngine):
    # Receivables have no error_code either, so cause_category also resolves
    # to abandoned_checkout - the category filter must keep this from
    # matching checkout_abandonment_first_time instead of the B2B rule.
    case = CaseContext(
        case_id="c6",
        customer_id="u6",
        category="receivable",
        abandons_last_7d=0,
        extra={"cause_category": "abandoned_checkout"},
    )
    result = engine.evaluate(case)
    assert result.matched_rule_id == "b2b_receivable_split_payment"
    assert "generate_split_payment_link" in result.allowed_actions
    assert "generate_payment_link" not in result.allowed_actions
