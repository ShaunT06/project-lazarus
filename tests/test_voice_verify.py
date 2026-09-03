from app.models import StrategyResult
from app.voice.verify import verify_utterance


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


def test_percentage_within_bound_passes():
    result = verify_utterance("I can offer you a 5% discount.", make_strategy())
    assert result.ok is True


def test_percentage_over_bound_blocked():
    result = verify_utterance("I can offer you a 50% discount.", make_strategy())
    assert result.ok is False
    assert result.findings[0]["rule"] == "PCT_EXCEEDS_ENVELOPE"
    assert result.findings[0]["claimed_pct"] == 50


def test_percentage_within_approved_this_call_passes():
    result = verify_utterance(
        "Confirming your 5% discount.",
        make_strategy(max_discount_pct=0),
        approved_discount_pct_this_call=5,
    )
    assert result.ok is True


def test_amount_over_bound_blocked():
    result = verify_utterance(
        "I can waive it down to Rs. 5000 for you.",
        make_strategy(),
        max_amount_paise=100_000,
    )
    assert result.ok is False
    rules = [f["rule"] for f in result.findings]
    assert "AMOUNT_EXCEEDS_ENVELOPE" in rules


def test_forbidden_word_blocked():
    result = verify_utterance(
        "I can guarantee this will be waived.", make_strategy(), never_say=("guarantee", "waive")
    )
    assert result.ok is False
    words = {f["word"] for f in result.findings if f["rule"] == "FORBIDDEN_WORD"}
    assert "guarantee" in words


def test_clean_utterance_passes():
    result = verify_utterance(
        "I understand this is frustrating - let's find a way forward.", make_strategy()
    )
    assert result.ok is True
    assert result.findings == []
