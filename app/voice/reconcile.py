"""Post-call reconciliation: an independent LLM pass that reads the raw
transcript and checks it against what record_commitment actually stored,
run after the call has already ended. This never rolls anything back on
its own - a mismatch is an audit.alert for a human to look at, the same
"flag, don't act" stance app/voice/session.py already takes for a
confirmation that failed to send. Skipped entirely for a call that never
reached a commitment - there is nothing to reconcile.
"""

import json
from typing import Any

from app.audit import AuditLogger
from app.voice.session import CallSession

_RECONCILE_TOOL = {
    "type": "function",
    "function": {
        "name": "reconciliation_verdict",
        "description": "Report whether the transcript matches the recorded commitment.",
        "parameters": {
            "type": "object",
            "properties": {
                "matches": {"type": "boolean"},
                "explanation": {"type": "string"},
            },
            "required": ["matches", "explanation"],
            "additionalProperties": False,
        },
    },
}


def _last_commitment(session: CallSession) -> dict[str, Any] | None:
    for msg in reversed(session.dialogue._messages):  # noqa: SLF001 - internal, same module family
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function", {})
            if fn.get("name") == "record_commitment":
                return json.loads(fn.get("arguments") or "{}")
    return None


def run(session: CallSession, audit: AuditLogger, chat_client: Any) -> dict[str, Any] | None:
    commitment = _last_commitment(session)
    if commitment is None:
        return None

    transcript_text = "\n".join(f"{t['role']}: {t['text']}" for t in session.transcript)
    messages = [
        {
            "role": "system",
            "content": (
                "Compare this call transcript against the commitment terms that were "
                "recorded. Call reconciliation_verdict with matches=false if the terms "
                "recorded do not match what the transcript shows was actually agreed."
            ),
        },
        {
            "role": "user",
            "content": (
                f"TRANSCRIPT:\n{transcript_text}\n\nRECORDED COMMITMENT:\n{json.dumps(commitment)}"
            ),
        },
    ]
    response = chat_client.chat(messages, tools=[_RECONCILE_TOOL])
    verdict_call = next(
        (tc for tc in response.tool_calls if tc.name == "reconciliation_verdict"), None
    )
    if verdict_call is None:
        result = {"matches": True, "explanation": "reconciliation model gave no verdict"}
    else:
        result = verdict_call.arguments

    if not result.get("matches", True):
        audit.log(
            session.case_id,
            "audit.alert",
            {
                "call_id": session.call_id,
                "reason": "post-call reconciliation found a transcript/commitment mismatch",
                "explanation": result.get("explanation"),
                "commitment": commitment,
            },
        )

    session.reconciliation = result
    audit.log(
        session.case_id,
        "call.reconciled",
        {"call_id": session.call_id, "result": result},
    )
    return result
