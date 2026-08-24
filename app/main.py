from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from app.audit import AuditLogger
from app.config import settings
from app.customer_store import CustomerStore
from app.openrouter_client import OpenRouterClient
from app.strategy import StrategyEngine
from app.webhook import build_webhook_router
from app.webhook_store import WebhookEventStore


def create_app(
    *,
    webhook_secret: str | None = None,
    db_path: Path | None = None,
    audit_path: Path | None = None,
    customer_db_path: Path | None = None,
    strategy_config_path: Path | None = None,
    openrouter_client_factory: Callable[[], Any] | None = None,
    max_gate_corrections: int | None = None,
    notify_channel: str | None = None,
) -> FastAPI:
    """App factory - takes explicit paths/secret so tests get isolated,
    ephemeral state instead of touching data/ on disk or global settings."""
    app = FastAPI(title="Project Lazarus")

    store = WebhookEventStore(db_path or Path("data/webhook_events.db"))
    audit = AuditLogger(audit_path or Path("data/audit.jsonl"))
    customer_store = CustomerStore(customer_db_path or Path("data/customers.db"))
    strategy_engine = StrategyEngine.from_file(
        strategy_config_path or settings.strategy_config_path
    )
    secret = webhook_secret if webhook_secret is not None else settings.razorpay_webhook_secret

    app.include_router(
        build_webhook_router(
            store=store,
            audit=audit,
            webhook_secret=secret,
            customer_store=customer_store,
            strategy_engine=strategy_engine,
            openrouter_client_factory=openrouter_client_factory or OpenRouterClient,
            max_gate_corrections=(
                max_gate_corrections
                if max_gate_corrections is not None
                else settings.max_gate_corrections
            ),
            notify_channel=notify_channel
            if notify_channel is not None
            else settings.notify_channel,
        )
    )

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    return app


app = create_app()
