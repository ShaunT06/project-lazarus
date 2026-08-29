"""Covers a bug found on a live batch run: a model-generated message
containing the Rupee sign (U+20B9) crashed print() on a Windows console
(cp1252), taking the whole batch down with it."""

import app.tools as tools_module
from app.tools import execute


def test_send_message_with_non_ascii_body_falls_back_safely(monkeypatch):
    # Simulate a console whose first write attempt can't represent U+20B9
    # (e.g. Windows cp1252) - the fallback write must still happen instead
    # of the exception propagating and taking the whole batch case down.
    calls: list[str] = []

    def fake_print(text: str) -> None:
        if len(calls) == 0:
            calls.append("raised")
            raise UnicodeEncodeError("cp1252", text, 0, 1, "bad char")
        calls.append(text)

    monkeypatch.setattr(tools_module, "print", fake_print, raising=False)

    result = execute(
        "send_message",
        {"body": "Your invoice of ₹210,000 is overdue."},
        notify_channel="console",
    )

    assert result == {"status": "sent", "channel": "console"}
    assert calls == ["raised", calls[1]]  # exactly one retry, and it succeeded
    assert "send_message:console" in calls[1]


def test_send_message_missing_body_does_not_crash():
    # A model can emit malformed tool arguments (see test_openrouter_client
    # for the case that produces {} here) - send_message must not KeyError.
    result = execute("send_message", {}, notify_channel="console")
    assert result == {"status": "sent", "channel": "console"}


def test_generate_split_payment_link_missing_installments_does_not_crash():
    result = execute("generate_split_payment_link", {})
    assert result["status"] == "created"
    assert result["installments"] is None
