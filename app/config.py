from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    # Paid, on a key with an explicit $4 hard spend cap - not the free tier
    # this project started on. Real cost verified before switching: about
    # $0.000006/call (prompt $0.000000075/tok, completion $0.00000025/tok),
    # so a full 50-case batch runs for a fraction of a cent - enormous
    # headroom under the cap. Chosen for latency: it's small/fast and
    # noticeably quicker per call than the free nemotron model, which also
    # shared a 50-req/day account-wide cap across every free model at once.
    # Verify current pricing/tool support via GET /api/v1/models
    # ("tools" in supported_parameters) before switching again.
    openrouter_model: str = "z-ai/glm-5.3-flash"

    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    razorpay_webhook_secret: str = ""

    strategy_config_path: Path = Path("config/strategy.example.json")
    max_gate_corrections: int = 3
    notify_channel: str = "console"

    # When set (Vercel deploy), all stores switch to Turso (libSQL) since
    # the serverless filesystem is read-only/ephemeral - SQLite files and
    # audit.jsonl do not survive between invocations there. Unset (local
    # dev, tests) keeps the original zero-dependency SQLite/JSONL path.
    # database_url is a libsql://<db>.turso.io URL; turso_auth_token is the
    # separate auth token Turso issues alongside it.
    database_url: str = ""
    turso_auth_token: str = ""

    # --- Voice channel (see docs/voice.md) ---
    # Phase 1 (gate/dialogue/verify/session/reconcile, text-simulated turns)
    # needs none of these - it runs on openrouter_api_key above. Phase 2
    # (real Sarvam STT/TTS over a browser WebRTC call) needs sarvam_api_key.
    # Phase 3 (ringing a real phone via Plivo) needs the plivo_* vars plus
    # public_base_url. All blank by default so the base app (webhook/chat/
    # dashboard) is completely unaffected until someone opts in.
    dialogue_policy_path: Path = Path("config/dialogue/default.json")
    voice_quiet_hours_start: int = 9  # local hour, inclusive
    voice_quiet_hours_end: int = 21  # local hour, exclusive
    voice_max_contacts_per_case: int = 4

    sarvam_api_key: str = ""
    sarvam_base_url: str = "https://api.sarvam.ai"
    # pipecat-ai 1.8.1's SarvamSTTService only accepts saaras:v3/v4 - the
    # older saarika:v2 name (used by some other Sarvam SDK versions/
    # lazarusV2's pinned pipecat) raises ValueError here. Confirmed via a
    # real WebRTC call that crashed on this before the fix.
    sarvam_stt_model: str = "saaras:v3"
    # bulbul:v2 is deprecated server-side as of this writing ("400: Model
    # 'bulbul:v2' has been deprecated. Please use 'bulbul:v3' instead.") -
    # confirmed by a real failed call before this fix; pipecat's own default
    # still points at v2, so this can't be left unset.
    sarvam_tts_model: str = "bulbul:v3"
    sarvam_tts_voice: str = ""

    plivo_auth_id: str = ""
    plivo_auth_token: str = ""
    plivo_from_number: str = ""
    public_base_url: str = ""
    # Dev-only escape hatch for when a corporate/tunnel proxy strips Plivo's
    # signature headers before they reach this process - off by default,
    # never set true in a real deployment. Mirrors lazarusV2's same knob.
    plivo_skip_signature_check: bool = False


settings = Settings()
