"""Postgres (Neon) connection helper, used only when settings.database_url
is set (the Vercel deploy). Local dev and tests never import this - they
stay on the SQLite/JSONL stores, which need no external service.

One short-lived connection per call, not a pool: each Vercel invocation is
a fresh, isolated process, so a long-lived pool buys nothing and Neon's
pooled connection string (the "-pooler" host) is what actually amortizes
connection setup across invocations.
"""

from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row

from app.config import settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS processed_events (
    event_id TEXT PRIMARY KEY,
    received_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS customers (
    customer_id TEXT PRIMARY KEY,
    ltv_inr DOUBLE PRECISION NOT NULL DEFAULT 0,
    marketing_opt_in BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS customer_events (
    id SERIAL PRIMARY KEY,
    customer_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    ts TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_customer_events_customer ON customer_events(customer_id);

CREATE TABLE IF NOT EXISTS audit_log (
    id SERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL,
    case_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_case ON audit_log(case_id);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts);

CREATE TABLE IF NOT EXISTS conversations (
    case_id TEXT PRIMARY KEY,
    case_context JSONB NOT NULL,
    cause_category TEXT NOT NULL,
    strategy JSONB NOT NULL,
    llm_messages JSONB NOT NULL,
    corrections INTEGER NOT NULL DEFAULT 0,
    approved_discount_pct DOUBLE PRECISION NOT NULL DEFAULT 0,
    gate_exhausted BOOLEAN NOT NULL DEFAULT FALSE,
    hard_stop BOOLEAN NOT NULL DEFAULT FALSE,
    hard_stop_reason TEXT,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS display_messages (
    id SERIAL PRIMARY KEY,
    case_id TEXT NOT NULL,
    role TEXT NOT NULL,
    kind TEXT NOT NULL,
    body TEXT,
    meta JSONB NOT NULL DEFAULT '{}'::jsonb,
    ts TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_display_messages_case ON display_messages(case_id);

CREATE TABLE IF NOT EXISTS strategy_config (
    id SERIAL PRIMARY KEY,
    config JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);
"""

_initialized = False


@contextmanager
def get_conn():
    conn = psycopg.connect(settings.database_url, row_factory=dict_row, autocommit=True)
    try:
        yield conn
    finally:
        conn.close()


def ensure_schema() -> None:
    """Idempotent; cheap enough to call on every cold start."""
    global _initialized
    if _initialized:
        return
    with get_conn() as conn:
        conn.execute(_SCHEMA)
    _initialized = True
