"""Audio transport: Pipecat over WebRTC (browser) or Plivo's Audio
Streaming websocket (real phone) - phase 2/3. Both paths build the same
pipeline shape around app/voice/session.py, just swapping the transport:

    mic/phone audio -> VAD -> Sarvam STT -> [ session.say() ] -> Sarvam TTS -> audio out

`available()` is checked by app/voice_routes.py before any route tries to
actually place a call - this module is safe to import even when the
`voice` extra (pipecat-ai, plivo) isn't installed; only calling
build_browser_pipeline()/build_plivo_pipeline() without it raises.

Real-time pipeline assembly (SmallWebRTCTransport / PlivoFrameSerializer,
Silero VAD, the frame-processor bridge into session.say()) lands once the
`voice` extra is confirmed installable in this environment - see
docs/voice.md for current status.
"""

from typing import Any

from app.config import settings

PIPECAT_HINT = "pipecat is not installed - pip install -e '.[voice]'"


def _imports() -> dict[str, Any]:
    try:
        from pipecat.audio.vad.silero import SileroVADAnalyzer  # noqa: F401
        from pipecat.pipeline.pipeline import Pipeline  # noqa: F401
        from pipecat.services.sarvam.stt import SarvamSTTService  # noqa: F401
        from pipecat.services.sarvam.tts import SarvamTTSService  # noqa: F401
        from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport  # noqa: F401
    except ImportError as exc:  # pragma: no cover - env dependent
        raise RuntimeError(PIPECAT_HINT) from exc
    return {}


def available() -> tuple[bool, str]:
    """Whether a real audio call can be placed right now, and why not if not."""
    try:
        _imports()
    except RuntimeError as exc:
        return False, str(exc)
    if not settings.sarvam_api_key:
        return False, "SARVAM_API_KEY is not set"
    return True, "ready"


def build_browser_pipeline(*_args: Any, **_kwargs: Any) -> Any:
    _imports()
    raise NotImplementedError(
        "browser WebRTC pipeline assembly lands in phase 2 - see docs/voice.md"
    )


def build_plivo_pipeline(*_args: Any, **_kwargs: Any) -> Any:
    _imports()
    raise NotImplementedError(
        "Plivo Audio Streaming pipeline assembly lands in phase 3 - see docs/voice.md"
    )
