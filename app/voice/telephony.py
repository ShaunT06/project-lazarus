"""Real PSTN audio over Plivo - phase 3. Plivo is a carrier only: it rings
an actual phone and streams audio back and forth over a websocket (Plivo's
Audio Streaming protocol). Speech itself stays Sarvam end to end, exactly
like lazarusV2's ADR-001 - Plivo never sees a phoneme, it only carries the
call. See app/voice/transport.py for what runs on top of that audio.

Every one of Plivo's webhook/callback routes is unauthenticated by
transport (Plivo calls them, nothing about the request proves that), so
validate_signature() is the real gate - it must be called before anything
in a Plivo request is trusted, on every one of those routes.

Lazily imports the `plivo` package so importing this module (and therefore
app.voice_routes, app.main) never fails just because the voice extra isn't
installed - available() is how a caller checks before actually dialling.
"""

from typing import Any

from app.config import settings

PLIVO_HINT = "the plivo package is not installed - pip install -e '.[voice]'"


def _imports() -> dict[str, Any]:
    try:
        import plivo
        from plivo import plivoxml
    except ImportError as exc:  # pragma: no cover - env dependent
        raise RuntimeError(PLIVO_HINT) from exc
    return {"plivo": plivo, "plivoxml": plivoxml}


def available() -> tuple[bool, str]:
    """Whether a real phone can be dialled right now, and why not if not."""
    try:
        _imports()
    except RuntimeError as exc:
        return False, str(exc)
    if not settings.plivo_auth_id or not settings.plivo_auth_token:
        return False, "PLIVO_AUTH_ID / PLIVO_AUTH_TOKEN not set"
    if not settings.plivo_from_number:
        return False, "PLIVO_FROM_NUMBER not set"
    if not settings.public_base_url:
        return False, "PUBLIC_BASE_URL not set - Plivo needs a URL of this server it can reach"
    if not settings.sarvam_api_key:
        return False, "SARVAM_API_KEY is not set"
    return True, "ready"


def ws_url(path: str) -> str:
    base = settings.public_base_url.rstrip("/")
    return base.replace("https://", "wss://").replace("http://", "ws://") + path


def http_url(path: str) -> str:
    """The public URL Plivo actually requests - also what its signature is
    computed against, so the webhook routes reconstruct this same URL
    before verifying one, rather than trusting whatever host header a
    proxy/tunnel handed FastAPI."""
    return settings.public_base_url.rstrip("/") + path


def validate_signature(
    url: str,
    method: str,
    params: dict[str, Any],
    signature: str | None,
    nonce: str | None,
) -> bool:
    if settings.plivo_skip_signature_check:
        return True
    if not signature or not nonce:
        return False
    P = _imports()
    return P["plivo"].utils.validate_v3_signature(
        method, url, nonce, settings.plivo_auth_token, signature, params
    )


def dial(call_id: str, to_number: str) -> str:
    """Ring a real phone for an already-placed call. Returns the Plivo call UUID.

    The CallSession must already exist (app.voice.session.place() run first)
    - this only tells Plivo's REST API to make the phone ring; the audio
    pipeline is built later, when Plivo's Audio Streaming websocket
    actually connects.
    """
    P = _imports()
    client = P["plivo"].RestClient(settings.plivo_auth_id, settings.plivo_auth_token)
    answer_url = http_url(f"/voice/plivo/answer/{call_id}")
    hangup_url = http_url(f"/voice/plivo/hangup/{call_id}")
    from_number = settings.plivo_from_number.lstrip("+")
    to = to_number.lstrip("+")

    response = client.calls.create(
        from_=from_number,
        to_=to,
        answer_url=answer_url,
        answer_method="POST",
        hangup_url=hangup_url,
        hangup_method="POST",
    )
    return response.request_uuid


def answer_xml(call_id: str) -> str:
    """PlivoXML connecting the answered call to this call's Audio Streaming
    websocket. contentType must be mu-law/8kHz - the frame serializer in
    transport.py decodes assuming exactly that."""
    P = _imports()
    plivoxml = P["plivoxml"]
    response = plivoxml.ResponseElement()
    stream = plivoxml.StreamElement(
        ws_url(f"/voice/plivo/stream/{call_id}"),
        bidirectional=True,
        keepCallAlive=True,
        contentType="audio/x-mulaw;rate=8000",
    )
    response.add(stream)
    return response.to_string()
