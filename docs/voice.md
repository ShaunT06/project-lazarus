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

## Phase 2 — real speech, browser call (done)

Real Sarvam STT/TTS over a Pipecat pipeline (VAD → STT → the exact same
`app/voice/session.say()` phase 1 uses → TTS), connected to the browser
over plain WebRTC.

**Install risk, resolved.** `pip install -e ".[voice]"`
(`pipecat-ai[webrtc,websocket,silero,sarvam]`, `plivo`) installs cleanly on
Windows with **prebuilt wheels for everything** — no torch, no FFmpeg, no
compiler toolchain needed (`av`, `aiortc`, `onnxruntime` all ship as
`win_amd64` wheels on pipecat-ai 1.8.1). Kept as a separate optional group
from the base install anyway, so the webhook/chat/dashboard/voice-phase-1
app works with zero installs from this group; `app/voice/transport.py`'s
`available()` reports exactly why it isn't ready if the extra or
`SARVAM_API_KEY` is missing.

**Two real bugs found only by actually placing a call** (neither is
catchable without a live Sarvam key):

1. `SarvamSTTService` in pipecat-ai 1.8.1 only accepts `saaras:v3`/`v4` —
   the `saarika:v2` model name (valid on some other Sarvam SDK versions)
   raises `ValueError` at construction time. Fixed: `sarvam_stt_model`
   defaults to `saaras:v3` (`app/config.py`).
2. Sarvam's `bulbul:v2` TTS model is deprecated **server-side** as of this
   writing (`400: Model 'bulbul:v2' has been deprecated. Please use
   'bulbul:v3' instead`) — pipecat's own default still points at v2. Fixed:
   `sarvam_tts_model` defaults to `bulbul:v3`.

**Verified end to end**, not just "compiles": the Claude Code Browser pane
blocks `getUserMedia` (confirmed via a real permission-denial error, which
`static/voice/index.html` now surfaces cleanly instead of failing
silently — a real bug caught and fixed along the way), so real audio was
verified with `scripts/verify_voice_call.py` instead — a scripted `aiortc`
client that does the same SDP offer/answer exchange a browser would, then
checks the *actual sample values* of the audio that comes back (not just
"frames arrived", which would also pass on pure silence padding). Confirmed:
Sarvam STT and TTS both authenticate and connect with the real key, the
WebRTC handshake completes, and real synthesized speech (max sample
amplitude ~10,000–14,000, `>200` threshold for "not silence") flows back
for the call's opening line. **Not yet verified**: live transcription
accuracy against real human speech — the scripted client sends silence (so
Sarvam STT has nothing to transcribe), and this needs a human tester with
a real microphone against `/voice` in an ordinary browser (not the Claude
Code Browser pane).

Try it: place a call the same way as phase 1 (`POST /api/voice/start`),
then either type into `/voice`'s composer (phase 1's path, unchanged) or
click "Connect real audio (mic)" to attach a live WebRTC call via
`POST /api/voice/{call_id}/offer` — both drive the exact same
`app/voice/session.say()`. Run `python scripts/verify_voice_call.py
<call_id>` against a running server to re-verify the pipeline without a
browser.

The browser side needs no client library: lazarusV2's
`frontend/client/src/audio.ts` is ~50 lines of plain
`RTCPeerConnection`/`getUserMedia`, ported directly into
`static/voice/index.html`'s `connectAudio()`.

## Phase 3 — ring a real phone (code done, live dial not yet verified)

Plivo REST dial + signature-verified webhooks + Audio Streaming WebSocket,
reusing the exact same `_assemble()` pipeline phase 2 built — only the
transport changes (`FastAPIWebsocketTransport` + `PlivoFrameSerializer`
instead of `SmallWebRTCTransport`).

| Piece | File | What it does |
|---|---|---|
| Outbound dial | `app/voice/telephony.py:dial()` | `plivo.RestClient(...).calls.create(...)` — rings a real number, points Plivo at `answer_url`/`hangup_url` |
| Answer webhook | `POST /voice/plivo/answer/{call_id}` | Signature-verified, returns PlivoXML (`telephony.answer_xml()`) pointing Plivo's Audio Streaming at the stream websocket |
| Hangup webhook | `POST /voice/plivo/hangup/{call_id}` | Signature-verified; ends the session as `no_answer` if Plivo never actually connected the call |
| Audio Streaming | `WS /voice/plivo/stream/{call_id}` | Drains Plivo's `start` event via pipecat's own `parse_telephony_websocket()` for `stream_id`/`call_id`, then hands off to `transport.run_plivo_call()` |
| Outbound trigger | `POST /api/voice/{call_id}/dial-phone` | `{"to_number": "+91..."}` — the call must already exist (`POST /api/voice/start` first, same precondition as phase 2's `/offer`) |

**Verified without live credentials**, the same way phase 2's signature
work was: `tests/test_voice_telephony.py::test_validate_signature_against_the_real_plivo_sdk`
computes a genuinely valid V3 signature using the real installed `plivo`
package's own signing function (not a mock), then confirms
`telephony.validate_signature()` accepts it, rejects a forged one, and
rejects a tampered form parameter — Plivo signs the POST body too
(`construct_post_url`), not just the URL and nonce, so a naive
implementation that ignored `params` would have passed a weaker version of
this test and still been wrong. Also cross-checked every function
signature in `telephony.py` (`RestClient.calls.create`,
`plivoxml.StreamElement`, `utils.validate_v3_signature`) against the
actually-installed `plivo` 4.62.0 SDK via introspection — all matched
what was written before the package was installed.

One correctness fix pulled from re-reading lazarusV2's reference
implementation: `FastAPIWebsocketParams` needs `allowed_origins=[]`
explicitly for the Plivo transport, because Plivo's Audio Streaming
connection carries no browser `Origin` header at all — pipecat's own
default here happens to already be permissive (driven by an unset
`PIPECAT_ALLOWED_ORIGINS` env var), but leaving it implicit means a future
deployment that sets that env var for the legitimate WebRTC path would
silently break Plivo calls too. Now explicit in `app/voice/transport.py`.

**Not yet verified**: an actual phone ringing. That needs, none of which
this environment has:

- `PLIVO_AUTH_ID`, `PLIVO_AUTH_TOKEN` — console.plivo.com → Account.
- `PLIVO_FROM_NUMBER` — buy one from console.plivo.com → Phone Numbers.
- `PUBLIC_BASE_URL` — a public HTTPS URL Plivo can reach. Locally that's a
  tunnel (`ngrok http 8000`); there is no Vercel option here (see below).

Once those are set: `POST /api/voice/start` → `POST /api/voice/{call_id}/dial-phone`
with a real `to_number` should ring it, and the same gate/dialogue/verify/
reconcile chain every other channel goes through applies over the PSTN leg.

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
