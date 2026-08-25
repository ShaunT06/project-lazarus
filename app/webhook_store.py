"""Persistent idempotency store for inbound webhook events.

SQLite, not in-memory: a redeploy or crash-restart must not replay an event
Razorpay has already delivered (webhooks are at-least-once).
"""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path


class WebhookEventStore:
    def __init__(self, db_path: Path):
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path)

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS processed_events (
                    event_id TEXT PRIMARY KEY,
                    received_at TEXT NOT NULL
                )
                """
            )

    def mark_processed_if_new(self, event_id: str) -> bool:
        """Atomically record event_id. Returns True if this is the first time
        we've seen it (caller should process), False if it's a duplicate."""
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO processed_events (event_id, received_at) VALUES (?, ?)",
                (event_id, datetime.now(UTC).isoformat()),
            )
            return cur.rowcount == 1


class TursoWebhookEventStore:
    """Same interface as WebhookEventStore, backed by Turso - used when
    settings.database_url is set (Vercel's filesystem is ephemeral). Same
    SQL as the SQLite class above - libSQL is a SQLite-compatible dialect."""

    def mark_processed_if_new(self, event_id: str) -> bool:
        from app.turso import get_client

        with get_client() as client:
            rs = client.execute(
                "INSERT OR IGNORE INTO processed_events (event_id, received_at) VALUES (?, ?)",
                (event_id, datetime.now(UTC).isoformat()),
            )
            return rs.rows_affected == 1
