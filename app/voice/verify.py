"""Verify-before-speak: every number the dialogue model wants to say is
checked against the frozen strategy bounds before it is allowed out,
whether that means out to a TTS engine (phase 2/3) or straight into the
transcript (phase 1's text-simulated turns). This is the voice lane's
equivalent of app/policy_gate.py's message-body scan for text - same
regex-scan idea, extended to also catch a rupee amount, not just a percent.

On failure, the caller gets back a deterministic fallback line instead of
the ungated text - the call keeps going, it just doesn't say the number
that wasn't cleared.
"""

import re
from dataclasses import dataclass, field
from typing import Any

from app.models import StrategyResult

_PCT_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_AMOUNT_PATTERN = re.compile(r"(?:₹|rs\.?|inr)\s*(\d+(?:,\d+)*(?:\.\d+)?)", re.IGNORECASE)


@dataclass
class VerifyResult:
    ok: bool
    findings: list[dict[str, Any]] = field(default_factory=list)


def verify_utterance(
    text: str,
    strategy: StrategyResult,
    *,
    approved_discount_pct_this_call: float = 0,
    max_amount_paise: int | None = None,
    never_say: tuple[str, ...] = (),
) -> VerifyResult:
    findings: list[dict[str, Any]] = []
    allowed_pct = max(strategy.max_discount_pct, approved_discount_pct_this_call)

    for match in _PCT_PATTERN.finditer(text):
        claimed = float(match.group(1))
        if claimed > allowed_pct:
            findings.append(
                {
                    "rule": "PCT_EXCEEDS_ENVELOPE",
                    "claimed_pct": claimed,
                    "allowed_pct": allowed_pct,
                }
            )

    if max_amount_paise is not None:
        for match in _AMOUNT_PATTERN.finditer(text):
            claimed_rupees = float(match.group(1).replace(",", ""))
            if claimed_rupees * 100 > max_amount_paise:
                findings.append(
                    {
                        "rule": "AMOUNT_EXCEEDS_ENVELOPE",
                        "claimed_paise": int(claimed_rupees * 100),
                        "allowed_paise": max_amount_paise,
                    }
                )

    lowered = text.lower()
    for word in never_say:
        if word in lowered:
            findings.append({"rule": "FORBIDDEN_WORD", "word": word})

    return VerifyResult(ok=not findings, findings=findings)
