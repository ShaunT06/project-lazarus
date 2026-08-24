from pathlib import Path

from fastapi import FastAPI

from app.audit import AuditLogger
from app.config import settings
from app.webhook import build_webhook_router
from app.webhook_store import WebhookEventStore


def create_app(
    *,
    webhook_secret: str | None = None,
    db_path: Path | None = None,
    audit_path: Path | None = None,
) -> FastAPI:
    """App factory - takes explicit paths/secret so tests get isolated,
    ephemeral state instead of touching data/ on disk or global settings."""
    app = FastAPI(title="Project Lazarus")

    store = WebhookEventStore(db_path or Path("data/webhook_events.db"))
    audit = AuditLogger(audit_path or Path("data/audit.jsonl"))
    secret = webhook_secret if webhook_secret is not None else settings.razorpay_webhook_secret

    app.include_router(build_webhook_router(store=store, audit=audit, webhook_secret=secret))

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    return app


app = create_app()
