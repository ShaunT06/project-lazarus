"""Tool schemas (OpenAI-style, what we send to OpenRouter) and executors.

Executors are mocked for now - Razorpay and WhatsApp integration are later
PRs. Signatures are the real ones so swapping the body for a live API call
later doesn't touch the agent loop or the policy gate.
"""

import sys
import uuid
from typing import Any


def _print_console_safe(text: str) -> None:
    """print() raises UnicodeEncodeError on Windows consoles (cp1252) for a
    message containing e.g. the Rupee sign - observed crashing a whole batch
    run on model-generated INR text. This is a display concern, not a
    business-logic failure, so degrade to a safe replacement rather than
    losing the run over it."""
    try:
        print(text)
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or "ascii"
        print(text.encode(encoding, errors="replace").decode(encoding, errors="replace"))


TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "send_message",
            "description": (
                "Send a recovery nudge to the customer. English only - no other "
                "language or code-switching. Do not state a discount or amount "
                "you have not already gotten approved via a tool call."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "body": {"type": "string", "description": "Message text, English only."},
                },
                "required": ["body"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_payment_link",
            "description": "Create a fresh payment link, optionally with an approved discount.",
            "parameters": {
                "type": "object",
                "properties": {
                    "discount_pct": {"type": "number", "description": "0 if no discount."},
                },
                "required": ["discount_pct"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_split_payment_link",
            "description": "Create a split/partial payment link for B2B receivables.",
            "parameters": {
                "type": "object",
                "properties": {
                    "installments": {"type": "integer"},
                },
                "required": ["installments"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "schedule_retry",
            "description": "Schedule an automatic retry of the payment.",
            "parameters": {
                "type": "object",
                "properties": {
                    "delay_hours": {"type": "number"},
                },
                "required": ["delay_hours"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_customer_record",
            "description": "Record an outcome or note against the customer's CRM record.",
            "parameters": {
                "type": "object",
                "properties": {
                    "note": {"type": "string"},
                },
                "required": ["note"],
                "additionalProperties": False,
            },
        },
    },
]

# The voice channel's closed in-call tool set (app/voice/session.py). Kept
# separate from TOOL_SCHEMAS deliberately - these three are reachable mid-call,
# never from the text agent loop, and none of them can settle a debt or send
# customer-facing text on their own. "record_commitment" only records terms;
# app/voice/session.py is what turns an agreed commitment into an actual
# confirmation message via the ordinary send_message tool above - "voice
# negotiates, text commits".
IN_CALL_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "record_commitment",
            "description": (
                "Record what the customer just agreed to on this call. Does not send "
                "anything by itself - a confirmation message is sent separately."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "lever": {
                        "type": "string",
                        "enum": ["discount", "split", "retry"],
                    },
                    "terms": {"type": "object"},
                },
                "required": ["lever", "terms"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "escalate_to_human",
            "description": "Hand this case to a human agent - the call cannot resolve it.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {"type": "string"},
                },
                "required": ["reason"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "suppress_contact",
            "description": "Stop all future outreach to this customer for this case.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {"type": "string"},
                },
                "required": ["reason"],
                "additionalProperties": False,
            },
        },
    },
]

IN_CALL_TOOL_NAMES = {s["function"]["name"] for s in IN_CALL_TOOL_SCHEMAS}


def execute(
    name: str, arguments: dict[str, Any], *, notify_channel: str = "console"
) -> dict[str, Any]:
    if name == "send_message":
        if notify_channel == "console":
            _print_console_safe(f"[send_message:console] {arguments.get('body', '')}")
        return {"status": "sent", "channel": notify_channel}
    if name == "generate_payment_link":
        return {
            "status": "created",
            "link": f"https://rzp.io/l/{uuid.uuid4().hex[:10]}",
            "discount_pct": arguments.get("discount_pct", 0),
        }
    if name == "generate_split_payment_link":
        return {
            "status": "created",
            "link": f"https://rzp.io/l/{uuid.uuid4().hex[:10]}",
            "installments": arguments.get("installments"),
        }
    if name == "schedule_retry":
        return {"status": "scheduled", "delay_hours": arguments.get("delay_hours")}
    if name == "update_customer_record":
        return {"status": "recorded"}
    if name == "record_commitment":
        return {
            "status": "recorded",
            "lever": arguments.get("lever"),
            "terms": arguments.get("terms", {}),
        }
    if name == "escalate_to_human":
        return {"status": "escalated", "reason": arguments.get("reason")}
    if name == "suppress_contact":
        return {"status": "suppressed", "reason": arguments.get("reason")}
    raise ValueError(f"unknown tool: {name}")
