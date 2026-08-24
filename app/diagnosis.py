"""Deterministic error_code -> cause_category mapping. No LLM involved.

The keys here are representative Razorpay failure reasons. Reconcile against
real `payment.failed` webhook payloads once we're pulling from test-mode
orders (plan.md phase: deterministic rule engines) — do not trust this list
blind for the batch run.
"""

_ERROR_REASON_MAP: dict[str, str] = {
    "insufficient_funds": "insufficient_funds",
    "card_declined": "card_declined",
    "expired_card": "expired_card",
    "invalid_card": "invalid_card",
    "authentication_failed": "authentication_failed",
    "otp_timeout": "authentication_failed",
    "issuer_down": "issuer_down",
    "bank_error": "issuer_down",
    "gateway_error": "gateway_timeout",
    "network_error": "network_error",
    "payment_cancelled": "user_cancelled",
    "risk_check_failed": "fraud_suspected",
}

UNKNOWN_CAUSE = "unknown"


def diagnose(error_code: str | None) -> str:
    """Map a Razorpay error reason/code to a cause_category.

    Returns UNKNOWN_CAUSE for unmapped or missing codes rather than raising -
    an unrecognized code is a data-quality fact worth logging, not a crash.
    """
    if not error_code:
        return UNKNOWN_CAUSE
    return _ERROR_REASON_MAP.get(error_code.strip().lower(), UNKNOWN_CAUSE)
