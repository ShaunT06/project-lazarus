# Voice channel

A third channel alongside the webhook pipeline and `/chat`: the same
diagnosis → strategy → agent → policy-gate architecture, shaped for a phone
call instead of a text thread. Modeled on `rahul-anb/lazarusV2`'s voice
channel (Sarvam STT/TTS over Pipecat/WebRTC, an optional Plivo PSTN bridge),
adapted onto this project's sync FastAPI/SQLite-or-Turso stack rather than
their async Postgres one.

Built in three phases, each independently demoable. This doc is updated as
phases land — like the README's status table, nothing here is aspirational.

## Phase 1 — deterministic core (done)

No audio, no new paid services. A customer's turn is typed into `/voice`
instead of spoken, but it runs through the *exact* architecture real audio
will call into later:

| Piece | File | What it does |
|---|---|---|
| Pre-dial gate | `app/voice/gate.py` | Hard stop, consent (`marketing_opt_in`), cooldown, quiet hours (09:00–21:00 local, configurable), contact budget — checked before a call is placed, same reasoning as `app/policy_gate.py` for text but earlier in the flow |
| Dialogue engine | `app/voice/dialogue.py` | One OpenRouter call per customer turn, closed lever menu computed from the frozen `StrategyResult`, `speak` + the 3 in-call tools as the only things a turn can do |
| Verify-before-speak | `app/voice/verify.py` | Regex-scans every "spoken" line for a %/₹ figure or forbidden word before it's accepted — extends `app/policy_gate.py`'s existing message-body scan |
| Call session | `app/voice/session.py` | `place()`/`say()`/`silence()`/`end()`, dispatches the 3 in-call tools (`record_commitment`, `escalate_to_human`, `suppress_contact` — added to `app/tools.py`) through the same `execute()` the text agent uses |
| Reconciliation | `app/voice/reconcile.py` | One more OpenRouter call after the call ends, diffing the transcript against the recorded commitment; logs `audit.alert` on a mismatch, never rolls anything back |

**"Voice negotiates, text commits"**: `record_commitment` only records
terms. `app/voice/session.py` immediately tries to send a confirmation
message through the ordinary `send_message` tool — re-validated by the
existing `app/policy_gate.py` first, same as any other tool call. If that
confirmation is rejected or fails, the call's outcome downgrades from
`agreed` to `agreed_unconfirmed` and an `audit.alert` is logged. Nothing
spoken on a call is binding on its own.

Try it: `POST /api/voice/start` with a `scenario_id` from
`GET /api/voice/scenarios` (same catalogue `/chat` uses), then
`POST /api/voice/{call_id}/turn` with `{"said": "..."}`. Or just open
`/voice` in a browser. Tests: `tests/test_voice_gate.py`,
`test_voice_verify.py`, `test_voice_dialogue.py`, `test_voice_session.py`,
`test_voice_telephony.py` (30 tests, all against a scripted fake LLM client
— no real OpenRouter calls in CI, same pattern as `test_agent_gate_loop.py`).

## Phase 2 — real speech, browser call (not started)

Sarvam STT/TTS wired through Pipecat's `SmallWebRTCTransport` + Silero VAD.
Needs:

- `pip install -e ".[voice]"` — a separate optional-dependency group
  (`pipecat-ai[webrtc,websocket,silero,sarvam]`, `plivo`), kept out of the
  base install because it pulls in `aiortc` and a torch-based VAD model, a
  heavy native dependency chain with known Windows install friction. The
  rest of the app (webhook/chat/dashboard/voice-phase-1) works with zero
  installs from this group; `app/voice/transport.py`'s `available()`
  reports exactly why it isn't ready if the extra is missing.
- `SARVAM_API_KEY` in `.env` — sign up at dashboard.sarvam.ai.

The browser side needs no client library: `frontend/client/src/audio.ts` in
lazarusV2 is ~50 lines of plain `RTCPeerConnection`/`getUserMedia`, portable
directly into `static/voice/index.html`.

## Phase 3 — ring a real phone (not started)

Plivo REST dial + signature-verified webhooks + Audio Streaming WebSocket
(`app/voice/telephony.py` already has the signature-verification and
PlivoXML-building logic, tested against a fake Plivo client in
`tests/test_voice_telephony.py` — only the actual pipeline wiring in
`app/voice/transport.py` is missing). Needs, in addition to phase 2's:

- `PLIVO_AUTH_ID`, `PLIVO_AUTH_TOKEN` — console.plivo.com → Account.
- `PLIVO_FROM_NUMBER` — buy one from console.plivo.com → Phone Numbers.
- `PUBLIC_BASE_URL` — a public HTTPS URL Plivo can reach. Locally that's a
  tunnel (`ngrok http 8000`); there is no Vercel option here (see below).

## Why this doesn't run on Vercel

`/chat` and `/dashboard` are deployed on Vercel, but Vercel's Python
functions are stateless request/response — no persistent WebSocket
connections, no in-memory session dict surviving between invocations.
Voice needs a long-running process, which is also how lazarusV2 runs it
(`make run` — local `uvicorn`, never described as deployed). So phases 2/3
run via local `uvicorn app.main:app --reload` (+ ngrok for phase 3), the
same as `/chat`/`/dashboard` already support in local dev — phase 1 (this
doc's "done" section) works identically either way, since it's plain
request/response with no persistent connection required.
