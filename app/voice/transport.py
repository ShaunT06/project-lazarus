"""Audio transport: Pipecat over WebRTC (browser, phase 2) or Plivo's Audio
Streaming WebSocket (a real phone, phase 3) - both build the identical
pipeline via `_assemble()`, just swapping the transport. Plivo is a
carrier only (see app/voice/telephony.py's module docstring): it rings a
real phone and moves audio over a websocket exactly where
`SmallWebRTCTransport` moves audio over WebRTC for the browser call.

The pipeline is deliberately thin:

    mic/phone audio -> VAD -> Sarvam STT -> [ session.say() ] -> Sarvam TTS -> audio out

There is no LLM node in that list on purpose (ADR-001 in lazarusV2, the
design this mirrors): the model is buried inside app/voice/dialogue.py,
behind the envelope clamp and verify.verify_utterance(). What flows out of
this pipeline is text that has already been cleared to be spoken.

Speech is Sarvam only, English-only - matches this project's existing
English-only convention for text (see app/tools.py's send_message), so
there's no Hinglish/script-mode handling to carry over from lazarusV2.

Written against pipecat-ai 1.8.1's actual API (confirmed via introspection,
not copied blind from lazarusV2's presumably-older pinned version) -
notably PipelineTask/PipelineRunner here, not the PipelineWorker/
WorkerRunner names an older pipecat used.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.audit import AuditLogger
from app.config import settings
from app.voice.session import CallSession

PIPECAT_HINT = "pipecat is not installed - pip install -e '.[voice]'"

# How long a call waits in silence before a nudge, and how many nudges
# before hanging up - mirrors dialogue.py's _MAX_SILENCES for the
# text-simulated path, just driven by a real idle timer here.
_IDLE_TIMEOUT_SECS = 10.0


def _imports() -> dict[str, Any]:
    # Every name below is re-exported through the returned dict, not used
    # directly in this function - that's the point (see the module
    # docstring), so ruff's unused-import check is a false positive here.
    try:
        from pipecat.audio.vad.silero import SileroVADAnalyzer  # noqa: F401
        from pipecat.audio.vad.vad_analyzer import VADParams  # noqa: F401
        from pipecat.frames.frames import (  # noqa: F401
            EndFrame,
            InterruptionFrame,
            TranscriptionFrame,
            TTSSpeakFrame,
            UserStartedSpeakingFrame,
        )
        from pipecat.pipeline.pipeline import Pipeline  # noqa: F401
        from pipecat.pipeline.runner import PipelineRunner  # noqa: F401
        from pipecat.pipeline.task import PipelineParams, PipelineTask  # noqa: F401
        from pipecat.processors.audio.vad_processor import VADProcessor  # noqa: F401
        from pipecat.processors.frame_processor import (  # noqa: F401
            FrameDirection,
            FrameProcessor,
        )
        from pipecat.runner.utils import parse_telephony_websocket  # noqa: F401
        from pipecat.serializers.plivo import PlivoFrameSerializer  # noqa: F401
        from pipecat.services.sarvam.stt import SarvamSTTService, SarvamSTTSettings  # noqa: F401
        from pipecat.services.sarvam.tts import SarvamTTSService, SarvamTTSSettings  # noqa: F401
        from pipecat.transcriptions.language import Language  # noqa: F401
        from pipecat.transports.base_transport import TransportParams  # noqa: F401
        from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection  # noqa: F401
        from pipecat.transports.smallwebrtc.request_handler import (  # noqa: F401
            SmallWebRTCRequest,
            SmallWebRTCRequestHandler,
        )
        from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport  # noqa: F401
        from pipecat.transports.websocket.fastapi import (  # noqa: F401
            FastAPIWebsocketParams,
            FastAPIWebsocketTransport,
        )
    except ImportError as exc:  # pragma: no cover - env dependent
        raise RuntimeError(PIPECAT_HINT) from exc
    return dict(locals())


def available() -> tuple[bool, str]:
    """Whether a real audio call can be placed right now, and why not if not."""
    try:
        _imports()
    except RuntimeError as exc:
        return False, str(exc)
    if not settings.sarvam_api_key:
        return False, "SARVAM_API_KEY is not set"
    return True, "ready"


def _stt_and_tts(P: dict[str, Any]) -> tuple[Any, Any]:
    lang = P["Language"].EN_IN
    stt = P["SarvamSTTService"](
        api_key=settings.sarvam_api_key,
        settings=P["SarvamSTTSettings"](model=settings.sarvam_stt_model, language=lang),
    )
    tts_settings = P["SarvamTTSSettings"](model=settings.sarvam_tts_model, language=lang)
    if settings.sarvam_tts_voice:
        tts_settings.voice = settings.sarvam_tts_voice
    tts = P["SarvamTTSService"](api_key=settings.sarvam_api_key, settings=tts_settings)
    return stt, tts


def _build_bridge(P: dict[str, Any]):
    """The one custom processor: a final transcript in, a cleared reply out.

    Runs app/voice/session.say() - the exact same function the text-
    simulated /voice endpoint calls - in a worker thread, since it's sync
    code (sync stores, sync OpenRouterClient) being driven from Pipecat's
    async pipeline. A fresh OpenRouterClient is created per turn rather
    than held across the call, matching how every other entry point in
    this project (app/chat.py, app/voice_routes.py) creates one per
    request rather than keeping a live client around as long-lived state.
    """
    FrameProcessor = P["FrameProcessor"]
    FrameDirection = P["FrameDirection"]
    TranscriptionFrame = P["TranscriptionFrame"]
    TTSSpeakFrame = P["TTSSpeakFrame"]
    EndFrame = P["EndFrame"]
    InterruptionFrame = P["InterruptionFrame"]
    UserStartedSpeakingFrame = P["UserStartedSpeakingFrame"]

    class _DialogueBridge(FrameProcessor):
        def __init__(
            self, call_session: CallSession, audit: AuditLogger, notify_channel: str, **kwargs
        ):
            super().__init__(**kwargs)
            self._call = call_session
            self._audit = audit
            self._notify_channel = notify_channel
            self._agent_speaking = False

        async def process_frame(self, frame, direction) -> None:
            await super().process_frame(frame, direction)

            if isinstance(frame, UserStartedSpeakingFrame):
                await self.push_frame(frame, direction)
                # Barge-in: a caller talking over the agent interrupts it
                # immediately rather than queueing behind the current line.
                if self._agent_speaking:
                    await self.push_frame(InterruptionFrame(), FrameDirection.DOWNSTREAM)
                return

            if not isinstance(frame, TranscriptionFrame) or not (frame.text or "").strip():
                await self.push_frame(frame, direction)
                return

            said = frame.text.strip()
            from app.openrouter_client import OpenRouterClient
            from app.voice import session as voice_session

            client = OpenRouterClient()
            try:
                turn = await asyncio.to_thread(
                    voice_session.say,
                    self._call,
                    said,
                    self._audit,
                    chat_client=client,
                    notify_channel=self._notify_channel,
                )
            except Exception:
                await self.push_frame(EndFrame(), FrameDirection.DOWNSTREAM)
                return
            finally:
                client.close()

            if turn.text:
                self._agent_speaking = True
                await self.push_frame(TTSSpeakFrame(turn.text), FrameDirection.DOWNSTREAM)
                self._agent_speaking = False

            if self._call.ended:
                await self.push_frame(EndFrame(), FrameDirection.DOWNSTREAM)

    return _DialogueBridge


def _assemble(
    P: dict[str, Any],
    transport: Any,
    call_session: CallSession,
    audit: AuditLogger,
    opening_text: str,
    *,
    notify_channel: str,
) -> tuple[Any, Any]:
    """The transport-agnostic half of a call: pipeline shape and event
    wiring. Phase 3's Plivo transport will share this once wired, the same
    way lazarusV2's telephony.py reuses transport._assemble."""
    stt, tts = _stt_and_tts(P)
    bridge = _build_bridge(P)(call_session, audit, notify_channel)
    vad_params = P["VADParams"]()

    pipeline = P["Pipeline"](
        [
            transport.input(),
            P["VADProcessor"](vad_analyzer=P["SileroVADAnalyzer"](params=vad_params)),
            stt,
            bridge,
            tts,
            transport.output(),
        ]
    )
    task = P["PipelineTask"](
        pipeline,
        params=P["PipelineParams"](),
        idle_timeout_secs=_IDLE_TIMEOUT_SECS,
        cancel_on_idle_timeout=False,
    )

    @task.event_handler("on_idle_timeout")
    async def _on_idle(_task):
        from app.voice import session as voice_session

        if call_session.ended:
            return
        turn = await asyncio.to_thread(voice_session.silence, call_session, audit)
        if turn.text:
            await task.queue_frame(P["TTSSpeakFrame"](turn.text))
        if call_session.ended:
            await task.queue_frame(P["EndFrame"]())

    @transport.event_handler("on_client_connected")
    async def _on_connected(_transport, _client):
        await task.queue_frame(P["TTSSpeakFrame"](opening_text))

    @transport.event_handler("on_client_disconnected")
    async def _on_disconnected(_transport, _client):
        from app.voice import session as voice_session

        if not call_session.ended:
            await asyncio.to_thread(voice_session.end, call_session, audit, "hung_up")

    runner = P["PipelineRunner"](handle_sigint=False)
    return task, runner


async def run_browser_call(
    webrtc_connection: Any,
    call_session: CallSession,
    audit: AuditLogger,
    opening_text: str,
    *,
    notify_channel: str = "console",
) -> None:
    """Build and run the pipeline for one browser call. Fire-and-forget:
    the caller (app/voice_routes.py) starts this as a background task and
    returns the SDP answer immediately - the call keeps running for as
    long as the browser tab stays connected."""
    P = _imports()
    transport = P["SmallWebRTCTransport"](
        webrtc_connection=webrtc_connection,
        params=P["TransportParams"](audio_in_enabled=True, audio_out_enabled=True),
    )
    task, runner = _assemble(
        P, transport, call_session, audit, opening_text, notify_channel=notify_channel
    )
    await runner.run(task)


async def run_plivo_call(
    websocket: Any,
    call_session: CallSession,
    audit: AuditLogger,
    opening_text: str,
    *,
    stream_id: str,
    plivo_call_id: str,
    notify_channel: str = "console",
) -> None:
    """Build and run the pipeline for one real phone call over Plivo's
    Audio Streaming websocket. `stream_id`/`plivo_call_id` come from
    Plivo's own `start` event, drained by
    `pipecat.runner.utils.parse_telephony_websocket` before this runs -
    `PlivoFrameSerializer` needs both at construction time (the latter
    only for auto hang-up via Plivo's REST API when the call ends).

    Same fire-and-forget contract as run_browser_call: the caller
    (app/voice_routes.py's websocket route) awaits this directly since a
    websocket route has nothing useful to return once the pipeline ends -
    unlike the browser's HTTP offer/answer exchange, there's no separate
    response to send back first.
    """
    P = _imports()
    serializer = P["PlivoFrameSerializer"](
        stream_id=stream_id,
        call_id=plivo_call_id,
        auth_id=settings.plivo_auth_id,
        auth_token=settings.plivo_auth_token,
    )
    transport = P["FastAPIWebsocketTransport"](
        websocket=websocket,
        params=P["FastAPIWebsocketParams"](
            audio_in_enabled=True,
            audio_out_enabled=True,
            add_wav_header=False,
            serializer=serializer,
            # Plivo's Audio Streaming connection carries no browser Origin
            # header at all - the allowlist exists for the WebRTC
            # signalling path, not this one. pipecat's own default
            # (PIPECAT_ALLOWED_ORIGINS, empty = allow all) already permits
            # this, but explicit is safer than depending on that env var
            # never being set restrictively in a real deployment.
            allowed_origins=[],
        ),
    )
    task, runner = _assemble(
        P, transport, call_session, audit, opening_text, notify_channel=notify_channel
    )
    await runner.run(task)
