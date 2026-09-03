"""Signature verification only - no real Plivo API call, and no dependency
on the `plivo` package actually being installed (it's in the optional
`voice` extra, see pyproject.toml). _imports() is monkeypatched so this
suite is meaningful whether or not that extra is present.
"""

import pytest

import app.voice.telephony as telephony


class FakePlivoUtils:
    def __init__(self, valid_signature: str):
        self._valid_signature = valid_signature

    def validate_v3_signature(self, method, url, nonce, auth_token, signature, params):
        return signature == self._valid_signature


class FakePlivo:
    def __init__(self, valid_signature: str = "correct-signature"):
        self.utils = FakePlivoUtils(valid_signature)


def test_missing_signature_or_nonce_fails_closed(monkeypatch):
    assert telephony.validate_signature("https://x/y", "POST", {}, None, "nonce") is False
    assert telephony.validate_signature("https://x/y", "POST", {}, "sig", None) is False


def test_valid_signature_passes(monkeypatch):
    monkeypatch.setattr(telephony, "_imports", lambda: {"plivo": FakePlivo()})
    monkeypatch.setattr(telephony.settings, "plivo_skip_signature_check", False)
    monkeypatch.setattr(telephony.settings, "plivo_auth_token", "secret")
    ok = telephony.validate_signature("https://x/y", "POST", {}, "correct-signature", "nonce-1")
    assert ok is True


def test_invalid_signature_fails(monkeypatch):
    monkeypatch.setattr(telephony, "_imports", lambda: {"plivo": FakePlivo()})
    monkeypatch.setattr(telephony.settings, "plivo_skip_signature_check", False)
    ok = telephony.validate_signature("https://x/y", "POST", {}, "wrong-signature", "nonce-1")
    assert ok is False


def test_skip_signature_check_bypasses_everything(monkeypatch):
    monkeypatch.setattr(telephony.settings, "plivo_skip_signature_check", True)
    assert telephony.validate_signature("https://x/y", "POST", {}, None, None) is True


def test_available_reports_missing_dependency_or_config():
    ok, reason = telephony.available()
    assert isinstance(ok, bool)
    assert isinstance(reason, str) and reason


def test_validate_signature_against_the_real_plivo_sdk(monkeypatch):
    """Not a mock - computes a genuinely valid V3 signature the same way
    plivo.utils.get_signature_v3 does, then confirms telephony.py's own
    validate_signature() (unpatched _imports, real installed plivo package)
    accepts it and rejects a tampered one. Skipped when the `voice` extra
    (and therefore `plivo`) isn't installed - matches every other lazy-
    import guard in this module."""
    plivo = pytest.importorskip("plivo")

    auth_token = "test-auth-token"
    nonce = "test-nonce-123"
    url = "https://example.ngrok-free.app/voice/plivo/answer/call_abc123"
    form = {"CallUUID": "abc-123", "To": "+15551234567", "From": "+15557654321"}

    base_url = plivo.utils.signature_v3.construct_post_url(url, dict(form)).decode("utf-8")
    real_signature = plivo.utils.signature_v3.get_signature_v3(
        auth_token.encode("utf-8"), base_url, nonce
    ).decode("utf-8")

    monkeypatch.setattr(telephony.settings, "plivo_skip_signature_check", False)
    monkeypatch.setattr(telephony.settings, "plivo_auth_token", auth_token)

    assert telephony.validate_signature(url, "POST", form, real_signature, nonce) is True
    assert telephony.validate_signature(url, "POST", form, "forged-signature", nonce) is False
    # Changing even one form field must invalidate the signature - Plivo
    # signs the params too (construct_post_url), not just the URL/nonce.
    tampered = {**form, "To": "+19998887777"}
    assert telephony.validate_signature(url, "POST", tampered, real_signature, nonce) is False
