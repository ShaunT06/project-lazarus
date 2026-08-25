from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    # Free-tier only, per explicit instruction - no paid model or API usage.
    # Verify current free + tool-capable models via GET /api/v1/models
    # (pricing.prompt == "0" and "tools" in supported_parameters) before
    # changing this, since OpenRouter's free lineup rotates.
    openrouter_model: str = "nvidia/nemotron-3-super-120b-a12b:free"

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
