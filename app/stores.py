"""Picks SQLite/JSONL (local dev, tests) vs Postgres/Neon (Vercel deploy)
for every store, based on settings.database_url. One place to look to know
which backend is live - every router builds its stores through here rather
than importing SQLite or Postgres classes directly.
"""

from pathlib import Path

from app.config import settings


def get_audit_logger(jsonl_path: Path):
    if settings.database_url:
        from app.audit import PostgresAuditLogger

        return PostgresAuditLogger()
    from app.audit import AuditLogger

    return AuditLogger(jsonl_path)


def get_webhook_store(db_path: Path):
    if settings.database_url:
        from app.webhook_store import PostgresWebhookEventStore

        return PostgresWebhookEventStore()
    from app.webhook_store import WebhookEventStore

    return WebhookEventStore(db_path)


def get_customer_store(db_path: Path):
    if settings.database_url:
        from app.customer_store import PostgresCustomerStore

        return PostgresCustomerStore()
    from app.customer_store import CustomerStore

    return CustomerStore(db_path)


def get_conversation_store(db_path: Path):
    if settings.database_url:
        from app.conversation_store import PostgresConversationStore

        return PostgresConversationStore()
    from app.conversation_store import ConversationStore

    return ConversationStore(db_path)


def get_strategy_config_store(seed_path: Path):
    if settings.database_url:
        from app.strategy_store import PostgresStrategyConfigStore

        return PostgresStrategyConfigStore(seed_path)
    from app.strategy_store import StrategyConfigStore

    return StrategyConfigStore(seed_path)
