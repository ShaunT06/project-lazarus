"""Picks SQLite/JSONL (local dev, tests) vs Turso/libSQL (Vercel deploy)
for every store, based on settings.database_url. One place to look to know
which backend is live - every router builds its stores through here rather
than importing SQLite or Turso classes directly.
"""

from pathlib import Path

from app.config import settings


def get_audit_logger(jsonl_path: Path):
    if settings.database_url:
        from app.audit import TursoAuditLogger

        return TursoAuditLogger()
    from app.audit import AuditLogger

    return AuditLogger(jsonl_path)


def get_webhook_store(db_path: Path):
    if settings.database_url:
        from app.webhook_store import TursoWebhookEventStore

        return TursoWebhookEventStore()
    from app.webhook_store import WebhookEventStore

    return WebhookEventStore(db_path)


def get_customer_store(db_path: Path):
    if settings.database_url:
        from app.customer_store import TursoCustomerStore

        return TursoCustomerStore()
    from app.customer_store import CustomerStore

    return CustomerStore(db_path)


def get_conversation_store(db_path: Path):
    if settings.database_url:
        from app.conversation_store import TursoConversationStore

        return TursoConversationStore()
    from app.conversation_store import ConversationStore

    return ConversationStore(db_path)


def get_strategy_config_store(seed_path: Path):
    if settings.database_url:
        from app.strategy_store import TursoStrategyConfigStore

        return TursoStrategyConfigStore(seed_path)
    from app.strategy_store import StrategyConfigStore

    return StrategyConfigStore(seed_path)
