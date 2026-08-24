from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_model: str = "anthropic/claude-sonnet-5"

    strategy_config_path: Path = Path("config/strategy.example.json")
    max_gate_corrections: int = 3
    notify_channel: str = "console"


settings = Settings()
