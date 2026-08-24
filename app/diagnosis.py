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
ABANDONED_CHECKOUT = "abandoned_checkout"


def diagnose(error_code: str | None) -> str:
    """Map a Razorpay error reason/code to a cause_category.

    No error_code at all means no payment was ever attempted - that's a
    genuine cart abandonment, not an unrecognized failure, so it maps to
    ABANDONED_CHECKOUT. A *present* but unmapped code falls to UNKNOWN_CAUSE
    instead of raising - an unrecognized code is a data-quality fact worth
    logging, not a crash.
    """
    if not error_code:
        return ABANDONED_CHECKOUT
    return _ERROR_REASON_MAP.get(error_code.strip().lower(), UNKNOWN_CAUSE)
