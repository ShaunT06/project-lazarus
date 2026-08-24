from app.diagnosis import UNKNOWN_CAUSE, diagnose


def test_known_codes_map_correctly():
    assert diagnose("insufficient_funds") == "insufficient_funds"
    assert diagnose("bank_error") == "issuer_down"
    assert diagnose("OTP_TIMEOUT") == "authentication_failed"


def test_unknown_or_missing_code_is_unknown():
    assert diagnose("some_new_gateway_code_2027") == UNKNOWN_CAUSE
    assert diagnose(None) == UNKNOWN_CAUSE
    assert diagnose("") == UNKNOWN_CAUSE
