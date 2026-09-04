from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse

from app.chat import build_chat_router
from app.config import settings
from app.dashboard import build_dashboard_router
from app.openrouter_client import OpenRouterClient
from app.stores import (
    get_audit_logger,
    get_conversation_store,
    get_customer_store,
    get_strategy_config_store,
    get_voice_call_store,
    get_webhook_store,
)
from app.strategy import StrategyEngine
from app.voice_routes import build_voice_router
from app.webhook import build_webhook_router

_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def create_app(
    *,
    webhook_secret: str | None = None,
    db_path: Path | None = None,
    audit_path: Path | None = None,
    customer_db_path: Path | None = None,
    conversation_db_path: Path | None = None,
    voice_call_db_path: Path | None = None,
    strategy_config_path: Path | None = None,
    openrouter_client_factory: Callable[[], Any] | None = None,
    max_gate_corrections: int | None = None,
    notify_channel: str | None = None,
    database_url: str | None = None,
) -> FastAPI:
    """App factory - takes explicit paths/secret so tests get isolated,
    ephemeral state instead of touching data/ on disk or global settings.

    Storage backend (SQLite/JSONL locally, Turso on Vercel) is picked by
    app.stores based on `database_url` here, which defaults to
    settings.database_url but can be overridden - tests pass "" explicitly
    so they stay hermetic (SQLite in tmp_path) regardless of whatever a
    developer's local .env happens to have set for real deployment use."""
    app = FastAPI(title="Project Lazarus")

    effective_database_url = database_url if database_url is not None else settings.database_url

    strategy_path = strategy_config_path or settings.strategy_config_path
    webhook_store = get_webhook_store(
        db_path or Path("data/webhook_events.db"), effective_database_url
    )
    audit = get_audit_logger(audit_path or Path("data/audit.jsonl"), effective_database_url)
    customer_store = get_customer_store(
        customer_db_path or Path("data/customers.db"), effective_database_url
    )
    conversation_store = get_conversation_store(
        conversation_db_path or Path("data/conversations.db"), effective_database_url
    )
    voice_call_store = get_voice_call_store(
        voice_call_db_path or Path("data/voice_calls.db"), effective_database_url
    )
    strategy_store = get_strategy_config_store(strategy_path, effective_database_url)
    strategy_engine = StrategyEngine.from_file(strategy_path)
    secret = webhook_secret if webhook_secret is not None else settings.razorpay_webhook_secret
    client_factory = openrouter_client_factory or OpenRouterClient
    corrections_cap = (
        max_gate_corrections if max_gate_corrections is not None else settings.max_gate_corrections
    )
    channel = notify_channel if notify_channel is not None else settings.notify_channel

    app.include_router(
        build_webhook_router(
            store=webhook_store,
            audit=audit,
            webhook_secret=secret,
            customer_store=customer_store,
            strategy_engine=strategy_engine,
            openrouter_client_factory=client_factory,
            max_gate_corrections=corrections_cap,
            notify_channel=channel,
        )
    )
    app.include_router(
        build_chat_router(
            conversation_store=conversation_store,
            customer_store=customer_store,
            audit=audit,
            strategy_store=strategy_store,
            openrouter_client_factory=client_factory,
            max_gate_corrections=corrections_cap,
            notify_channel=channel,
        )
    )
    app.include_router(
        build_dashboard_router(
            conversation_store=conversation_store,
            audit=audit,
            strategy_store=strategy_store,
        )
    )
    app.include_router(
        build_voice_router(
            voice_call_store=voice_call_store,
            customer_store=customer_store,
            audit=audit,
            strategy_store=strategy_store,
            openrouter_client_factory=client_factory,
        )
    )

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.get("/dashboard")
    def dashboard_page():
        return FileResponse(_STATIC_DIR / "dashboard" / "index.html")

    @app.get("/chat")
    def chat_page():
        return FileResponse(_STATIC_DIR / "chat" / "index.html")

    @app.get("/voice")
    def voice_page():
        return FileResponse(_STATIC_DIR / "voice" / "index.html")

    @app.get("/", response_class=HTMLResponse)
    def index():
        return (
            "<!doctype html><html><head><title>Project Lazarus</title>"
            "<style>body{font-family:system-ui,sans-serif;max-width:32rem;"
            "margin:4rem auto;line-height:1.6}a{display:block;margin:.5rem 0}"
            "</style></head><body>"
            "<h1>Project Lazarus</h1>"
            "<p>AI-powered revenue recovery agent.</p>"
            "<a href='/chat'>Customer chat (Lazarus talking to a customer)</a>"
            "<a href='/dashboard'>Merchant dashboard (audit &amp; metrics)</a>"
            "</body></html>"
        )

    return app


app = create_app()
