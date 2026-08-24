from app.diagnosis import ABANDONED_CHECKOUT, UNKNOWN_CAUSE, diagnose


def test_known_codes_map_correctly():
    assert diagnose("insufficient_funds") == "insufficient_funds"
    assert diagnose("bank_error") == "issuer_down"
    assert diagnose("OTP_TIMEOUT") == "authentication_failed"


def test_unrecognized_code_is_unknown():
    assert diagnose("some_new_gateway_code_2027") == UNKNOWN_CAUSE


def test_missing_code_means_abandoned_checkout():
    # No error_code at all means no payment was attempted - that's a real
    # cart abandonment, not an unrecognized error.
    assert diagnose(None) == ABANDONED_CHECKOUT
    assert diagnose("") == ABANDONED_CHECKOUT
