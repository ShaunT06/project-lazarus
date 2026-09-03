"""Signature verification only - no real Plivo API call, and no dependency
on the `plivo` package actually being installed (it's in the optional
`voice` extra, see pyproject.toml). _imports() is monkeypatched so this
suite is meaningful whether or not that extra is present.
"""

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
