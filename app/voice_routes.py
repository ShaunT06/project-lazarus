"""API for the voice channel (/voice). Phase 1: text-simulated turns
through the exact gate -> dialogue -> verify -> session loop real audio
will call into later, so the architecture is demoable and testable before
Sarvam/Pipecat/Plivo are wired. Phase 2/3 endpoints exist here too but
return 503 with the specific missing-dependency reason (see
app.voice.transport.available() / app.voice.telephony.available()) until
those extras are installed and configured - never a silent no-op.

Reuses app.chat's SCENARIO catalogue rather than duplicating it - a
"payment failed" scenario means the same thing whether the customer is
about to type or talk.
"""

import asyncio
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request, WebSocket
from fastapi.responses import Response
from pydantic import BaseModel

from app.audit import AuditLogger
from app.chat import _SCENARIOS_BY_ID, SCENARIOS, _llm_error_detail
from app.config import settings
from app.customer_store import CustomerStore, TursoCustomerStore
from app.diagnosis import diagnose
from app.models import CaseContext
from app.strategy import StrategyEngine
from app.strategy_store import StrategyConfigStore, TursoStrategyConfigStore
from app.voice import reconcile, telephony, transport
from app.voice import session as voice_session
from app.voice.policy import load_dialogue_policy
from app.voice_store import TursoVoiceCallStore, VoiceCallStore


class StartCallRequest(BaseModel):
    scenario_id: str


class TurnRequest(BaseModel):
    said: str


class DialPhoneRequest(BaseModel):
    to_number: str


# Lazily built the first time a real audio call is actually offered, so
# importing this module never requires pipecat to be installed - only
# placing a real call does. One handler per process, since it tracks every
# live peer connection by pc_id (pipecat's own SmallWebRTCRequestHandler
# design, not something this project added).
_webrtc_handler_holder: dict[str, Any] = {}


def _get_webrtc_handler(transport_module) -> Any:
    if "handler" not in _webrtc_handler_holder:
        P = transport_module._imports()
        _webrtc_handler_holder["handler"] = P["SmallWebRTCRequestHandler"]()
    return _webrtc_handler_holder["handler"]


def build_voice_router(
    *,
    voice_call_store: VoiceCallStore | TursoVoiceCallStore,
    customer_store: CustomerStore | TursoCustomerStore,
    audit: AuditLogger,
    strategy_store: StrategyConfigStore | TursoStrategyConfigStore,
    openrouter_client_factory,
    dialogue_policy_path: Any = None,
) -> APIRouter:
    router = APIRouter()
    policy = load_dialogue_policy(dialogue_policy_path or settings.dialogue_policy_path)

    def _save(call_session: voice_session.CallSession) -> None:
        voice_call_store.save(call_session.public())

    @router.get("/api/voice/scenarios")
    def list_scenarios():
        return [{k: v for k, v in s.items() if k != "force_abandons_last_7d"} for s in SCENARIOS]

    @router.get("/api/voice/status")
    def status():
        browser_ok, browser_reason = transport.available()
        phone_ok, phone_reason = telephony.available()
        return {
            "browser_call": {"available": browser_ok, "reason": browser_reason},
            "phone_call": {"available": phone_ok, "reason": phone_reason},
        }

    @router.post("/api/voice/start")
    def start(req: StartCallRequest):
        scenario = _SCENARIOS_BY_ID.get(req.scenario_id)
        if scenario is None:
            raise HTTPException(status_code=404, detail="unknown scenario_id")

        case_id = f"voice_{uuid.uuid4().hex[:10]}"
        customer_id = f"voice_cust_{uuid.uuid4().hex[:8]}"
        if scenario.get("force_abandons_last_7d"):
            customer_store.record_abandon_event(customer_id)
            customer_store.record_abandon_event(customer_id)
        customer_store.record_abandon_event(customer_id)
        abandons = customer_store.abandons_last_7d(customer_id)

        case = CaseContext(
            case_id=case_id,
            customer_id=customer_id,
            customer_ltv_inr=scenario["customer_ltv_inr"],
            abandons_last_7d=abandons,
            marketing_opt_in=True,
            hours_since_last_outreach=999,
            error_code=scenario["error_code"],
            cart_amount_inr=scenario["cart_amount_inr"],
            category=scenario["category"],
            is_synthetic=True,
            extra={"data_source": "simulated_by_voice_ui", "scenario_id": scenario["id"]},
        )
        cause_category = diagnose(case.error_code)
        case.extra["cause_category"] = cause_category

        strategy_engine = StrategyEngine(strategy_store.get())
        strategy_result = strategy_engine.evaluate(case)

        try:
            call_session, _opening = voice_session.place(
                case,
                strategy_result,
                policy,
                audit,
                now=datetime.now(),
                max_contacts=settings.voice_max_contacts_per_case,
            )
        except voice_session.GateRefused as exc:
            raise HTTPException(
                status_code=409, detail={"blockers": exc.blockers, "reasons": exc.reasons}
            ) from exc

        _save(call_session)
        return {"case_id": case_id, **call_session.public()}

    @router.post("/api/voice/{call_id}/turn")
    def turn(call_id: str, req: TurnRequest):
        call_session = voice_session.get_session(call_id)
        if call_session is None:
            raise HTTPException(status_code=404, detail="unknown call_id, or the call has ended")

        client = openrouter_client_factory()
        try:
            try:
                voice_session.say(
                    call_session,
                    req.said,
                    audit,
                    chat_client=client,
                    notify_channel=settings.notify_channel,
                )
                if call_session.ended:
                    reconcile.run(call_session, audit, client)
            except Exception as exc:
                audit.log(call_session.case_id, "pipeline_error", {"error": str(exc)})
                raise HTTPException(status_code=503, detail=_llm_error_detail(exc)) from exc
        finally:
            client.close()

        _save(call_session)
        return call_session.public()

    @router.post("/api/voice/{call_id}/offer")
    async def call_offer(call_id: str, req: dict):
        """WebRTC SDP offer/answer exchange - phase 2. The call must already
        exist (POST /api/voice/start first, same as the text-simulated
        path) - this only attaches real audio to it. From here on, every
        customer utterance reaches app/voice/session.say() through
        app/voice/transport.py's pipeline instead of this router's /turn
        endpoint, but it's the exact same function either way."""
        ok, reason = transport.available()
        if not ok:
            raise HTTPException(status_code=503, detail=reason)

        call_session = voice_session.get_session(call_id)
        if call_session is None:
            raise HTTPException(status_code=404, detail="unknown call_id, or the call has ended")
        if not call_session.transcript:
            raise HTTPException(status_code=409, detail="call has no opening turn yet")

        P = transport._imports()
        webrtc_request = P["SmallWebRTCRequest"].from_dict(req)
        opening_text = call_session.transcript[0]["text"]

        async def _on_connection(connection: Any) -> None:
            asyncio.create_task(
                transport.run_browser_call(
                    connection,
                    call_session,
                    audit,
                    opening_text,
                    notify_channel=settings.notify_channel,
                )
            )

        handler = _get_webrtc_handler(transport)
        answer = await handler.handle_web_request(webrtc_request, _on_connection)
        return answer

    @router.post("/api/voice/{call_id}/dial-phone")
    def dial_phone(call_id: str, req: DialPhoneRequest):
        """Ring a real phone for an already-placed call - phase 3. Same
        precondition as /offer: POST /api/voice/start must have run first,
        so the gate has already passed and there's an opening line ready
        to speak once Plivo's Audio Streaming websocket connects."""
        ok, reason = telephony.available()
        if not ok:
            raise HTTPException(status_code=503, detail=reason)

        call_session = voice_session.get_session(call_id)
        if call_session is None:
            raise HTTPException(status_code=404, detail="unknown call_id, or the call has ended")

        plivo_call_uuid = telephony.dial(call_id, req.to_number)
        audit.log(
            call_session.case_id,
            "call.dial_phone",
            {"call_id": call_id, "to_number": req.to_number, "plivo_call_uuid": plivo_call_uuid},
        )
        return {"plivo_call_uuid": plivo_call_uuid}

    def _verify_plivo_request(path: str, form: dict, headers) -> None:
        """Shared signature check for both Plivo webhook routes. Every one
        of them is unauthenticated by transport (Plivo just POSTs to a
        public URL), so this is the actual gate - raises 403 rather than
        trusting anything in the request first."""
        url = telephony.http_url(path)
        signature = headers.get("X-Plivo-Signature-V3")
        nonce = headers.get("X-Plivo-Signature-V3-Nonce")
        if not telephony.validate_signature(url, "POST", form, signature, nonce):
            raise HTTPException(status_code=403, detail="invalid or missing Plivo signature")

    @router.post("/voice/plivo/answer/{call_id}")
    async def plivo_answer(call_id: str, request: Request):
        ok, reason = telephony.available()
        if not ok:
            raise HTTPException(status_code=503, detail=reason)
        form = dict(await request.form())
        _verify_plivo_request(f"/voice/plivo/answer/{call_id}", form, request.headers)

        call_session = voice_session.get_session(call_id)
        if call_session is None:
            raise HTTPException(status_code=404, detail="unknown call_id, or the call has ended")

        return Response(content=telephony.answer_xml(call_id), media_type="text/xml")

    @router.post("/voice/plivo/hangup/{call_id}")
    async def plivo_hangup(call_id: str, request: Request):
        ok, reason = telephony.available()
        if not ok:
            raise HTTPException(status_code=503, detail=reason)
        form = dict(await request.form())
        _verify_plivo_request(f"/voice/plivo/hangup/{call_id}", form, request.headers)

        call_session = voice_session.get_session(call_id)
        if call_session is not None and not call_session.ended:
            # Plivo never connected the call (no answer, busy, failed) - end
            # it cleanly so the case doesn't wedge in "negotiating" forever.
            voice_session.end(call_session, audit, "no_answer")
            _save(call_session)
        return {"status": "ok"}

    @router.websocket("/voice/plivo/stream/{call_id}")
    async def plivo_stream(websocket: WebSocket, call_id: str):
        """Plivo's actual Audio Streaming connection. Drains the `start`
        event for stream_id/call_id (needed by PlivoFrameSerializer) before
        handing the socket to the same pipeline app/voice/transport.py
        builds for a browser call."""
        await websocket.accept()
        ok, _reason = transport.available()
        call_session = voice_session.get_session(call_id)
        if not ok or call_session is None or not call_session.transcript:
            await websocket.close()
            return

        P = transport._imports()
        _transport_type, call_data = await P["parse_telephony_websocket"](websocket)
        opening_text = call_session.transcript[0]["text"]
        await transport.run_plivo_call(
            websocket,
            call_session,
            audit,
            opening_text,
            stream_id=call_data.stream_id,
            plivo_call_id=call_data.call_id,
            notify_channel=settings.notify_channel,
        )
        _save(call_session)

    @router.get("/api/voice/calls/{call_id}")
    def get_call(call_id: str):
        call_session = voice_session.get_session(call_id)
        if call_session is not None:
            return call_session.public()
        stored = voice_call_store.get(call_id)
        if stored is None:
            raise HTTPException(status_code=404, detail="unknown call_id")
        return stored

    @router.get("/api/voice/calls")
    def list_calls(limit: int = 100):
        return voice_call_store.list_calls(limit=limit)

    return router
