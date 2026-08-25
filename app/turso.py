"""Turso (libSQL) connection helper, used only when settings.database_url
is set (the Vercel deploy). Local dev and tests never import this - they
stay on stdlib sqlite3/JSONL, which need no external service.

libSQL is a SQLite fork with a wire-compatible dialect, so every query
here is the same `?`-placeholder SQL the local SQLite classes use (see
audit.py/webhook_store.py/customer_store.py/conversation_store.py/
strategy_store.py for the pairs) - only the connection source changes.
One short-lived client per call, not a pool: each Vercel invocation is a
fresh, isolated process, so there's nothing to pool across requests.
"""

from contextlib import contextmanager

import libsql_client

from app.config import settings

_SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS processed_events (
        event_id TEXT PRIMARY KEY,
        received_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS customers (
        customer_id TEXT PRIMARY KEY,
        ltv_inr REAL NOT NULL DEFAULT 0,
        marketing_opt_in INTEGER NOT NULL DEFAULT 1
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS customer_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_id TEXT NOT NULL,
        event_type TEXT NOT NULL,
        ts TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_customer_events_customer ON customer_events(customer_id)",
    """
    CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        case_id TEXT NOT NULL,
        event_type TEXT NOT NULL,
        payload TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_audit_case ON audit_log(case_id)",
    "CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts)",
    """
    CREATE TABLE IF NOT EXISTS conversations (
        case_id TEXT PRIMARY KEY,
        case_context TEXT NOT NULL,
        cause_category TEXT NOT NULL,
        strategy TEXT NOT NULL,
        llm_messages TEXT NOT NULL,
        corrections INTEGER NOT NULL DEFAULT 0,
        approved_discount_pct REAL NOT NULL DEFAULT 0,
        gate_exhausted INTEGER NOT NULL DEFAULT 0,
        hard_stop INTEGER NOT NULL DEFAULT 0,
        hard_stop_reason TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS display_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        case_id TEXT NOT NULL,
        role TEXT NOT NULL,
        kind TEXT NOT NULL,
        body TEXT,
        meta TEXT NOT NULL DEFAULT '{}',
        ts TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_display_messages_case ON display_messages(case_id)",
    """
    CREATE TABLE IF NOT EXISTS strategy_config (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        config TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
]

_initialized = False


@contextmanager
def get_client():
    client = libsql_client.create_client_sync(
        settings.database_url, auth_token=settings.turso_auth_token
    )
    try:
        yield client
    finally:
        client.close()


def ensure_schema() -> None:
    """Idempotent; cheap enough to call on every cold start."""
    global _initialized
    if _initialized:
        return
    with get_client() as client:
        client.batch(_SCHEMA_STATEMENTS)
    _initialized = True
