"""Tool schemas (OpenAI-style, what we send to OpenRouter) and executors.

Executors are mocked for now - Razorpay and WhatsApp integration are later
PRs. Signatures are the real ones so swapping the body for a live API call
later doesn't touch the agent loop or the policy gate.
"""

import uuid
from typing import Any

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


def execute(
    name: str, arguments: dict[str, Any], *, notify_channel: str = "console"
) -> dict[str, Any]:
    if name == "send_message":
        if notify_channel == "console":
            print(f"[send_message:console] {arguments['body']}")
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
    raise ValueError(f"unknown tool: {name}")
