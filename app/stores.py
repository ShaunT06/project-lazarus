"""Picks SQLite/JSONL (local dev, tests) vs Turso/libSQL (Vercel deploy)
for every store, based on the `database_url` each factory is given. One
place to look to know which backend is live - every router builds its
stores through here rather than importing SQLite or Turso classes
directly. Callers (app/main.py) decide what `database_url` to pass -
usually settings.database_url, but tests pass "" explicitly so a
developer's local .env can't silently redirect them to a real Turso
instance.
"""

from pathlib import Path


def get_audit_logger(jsonl_path: Path, database_url: str):
    if database_url:
        from app.audit import TursoAuditLogger

        return TursoAuditLogger()
    from app.audit import AuditLogger

    return AuditLogger(jsonl_path)


def get_webhook_store(db_path: Path, database_url: str):
    if database_url:
        from app.webhook_store import TursoWebhookEventStore

        return TursoWebhookEventStore()
    from app.webhook_store import WebhookEventStore

    return WebhookEventStore(db_path)


def get_customer_store(db_path: Path, database_url: str):
    if database_url:
        from app.customer_store import TursoCustomerStore

        return TursoCustomerStore()
    from app.customer_store import CustomerStore

    return CustomerStore(db_path)


def get_conversation_store(db_path: Path, database_url: str):
    if database_url:
        from app.conversation_store import TursoConversationStore

        return TursoConversationStore()
    from app.conversation_store import ConversationStore

    return ConversationStore(db_path)


def get_strategy_config_store(seed_path: Path, database_url: str):
    if database_url:
        from app.strategy_store import TursoStrategyConfigStore

        return TursoStrategyConfigStore(seed_path)
    from app.strategy_store import StrategyConfigStore

    return StrategyConfigStore(seed_path)


def get_voice_call_store(db_path: Path, database_url: str):
    if database_url:
        from app.voice_store import TursoVoiceCallStore

        return TursoVoiceCallStore()
    from app.voice_store import VoiceCallStore

    return VoiceCallStore(db_path)
