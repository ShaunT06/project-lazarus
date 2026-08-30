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


settings = Settings()
