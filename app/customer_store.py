"""Customer profile/history store backing the strategy engine's inputs
(customer_ltv_inr, abandons_last_7d, marketing_opt_in, hours_since_last_outreach).

Not the plan.md "state serialization" phase - that's cart/session data for
WhatsApp checkout hydration, still deferred. This is the minimum customer
history needed to evaluate the strategy config at all. LTV and opt-in
default to placeholders (0, True) until backfilled from a real CRM - that
backfill is out of scope here.
"""

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path


class CustomerStore:
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
                CREATE TABLE IF NOT EXISTS customers (
                    customer_id TEXT PRIMARY KEY,
                    ltv_inr REAL NOT NULL DEFAULT 0,
                    marketing_opt_in INTEGER NOT NULL DEFAULT 1
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    customer_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    ts TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_customer ON events(customer_id)")

    def get_profile(self, customer_id: str) -> dict:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT ltv_inr, marketing_opt_in FROM customers WHERE customer_id = ?",
                (customer_id,),
            ).fetchone()
        if row is None:
            return {"ltv_inr": 0.0, "marketing_opt_in": True}
        return {"ltv_inr": row[0], "marketing_opt_in": bool(row[1])}

    def record_abandon_event(self, customer_id: str) -> None:
        """Records this case as an abandonment signal for the customer.
        Called before reading abandons_last_7d so the current case counts
        toward its own hard-stop threshold (the 3rd abandonment this week
        is itself the one that should trigger the stop)."""
        with self._connect() as conn:
            conn.execute("INSERT OR IGNORE INTO customers (customer_id) VALUES (?)", (customer_id,))
            conn.execute(
                "INSERT INTO events (customer_id, event_type, ts) VALUES (?, 'abandon', ?)",
                (customer_id, datetime.now(UTC).isoformat()),
            )

    def record_outreach_event(self, customer_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO events (customer_id, event_type, ts) VALUES (?, 'outreach', ?)",
                (customer_id, datetime.now(UTC).isoformat()),
            )

    def abandons_last_7d(self, customer_id: str) -> int:
        cutoff = (datetime.now(UTC) - timedelta(days=7)).isoformat()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM events "
                "WHERE customer_id = ? AND event_type = 'abandon' AND ts >= ?",
                (customer_id, cutoff),
            ).fetchone()
        return row[0] if row else 0

    def hours_since_last_outreach(self, customer_id: str) -> float:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT ts FROM events WHERE customer_id = ? AND event_type = 'outreach' "
                "ORDER BY ts DESC LIMIT 1",
                (customer_id,),
            ).fetchone()
        if row is None:
            return 999.0
        last = datetime.fromisoformat(row[0])
        return (datetime.now(UTC) - last).total_seconds() / 3600


class TursoCustomerStore:
    """Same interface as CustomerStore, backed by Turso - used when
    settings.database_url is set (Vercel's filesystem is ephemeral). Same
    SQL as the SQLite class above - libSQL is a SQLite-compatible dialect."""

    def __init__(self):
        from app.turso import ensure_schema

        ensure_schema()

    def get_profile(self, customer_id: str) -> dict:
        from app.turso import get_client

        with get_client() as client:
            rs = client.execute(
                "SELECT ltv_inr, marketing_opt_in FROM customers WHERE customer_id = ?",
                (customer_id,),
            )
        if not rs.rows:
            return {"ltv_inr": 0.0, "marketing_opt_in": True}
        row = rs.rows[0]
        return {"ltv_inr": row["ltv_inr"], "marketing_opt_in": bool(row["marketing_opt_in"])}

    def record_abandon_event(self, customer_id: str) -> None:
        from app.turso import get_client

        with get_client() as client:
            client.execute(
                "INSERT OR IGNORE INTO customers (customer_id) VALUES (?)", (customer_id,)
            )
            client.execute(
                "INSERT INTO customer_events (customer_id, event_type, ts) "
                "VALUES (?, 'abandon', ?)",
                (customer_id, datetime.now(UTC).isoformat()),
            )

    def record_outreach_event(self, customer_id: str) -> None:
        from app.turso import get_client

        with get_client() as client:
            client.execute(
                "INSERT INTO customer_events (customer_id, event_type, ts) "
                "VALUES (?, 'outreach', ?)",
                (customer_id, datetime.now(UTC).isoformat()),
            )

    def abandons_last_7d(self, customer_id: str) -> int:
        from app.turso import get_client

        cutoff = (datetime.now(UTC) - timedelta(days=7)).isoformat()
        with get_client() as client:
            rs = client.execute(
                "SELECT COUNT(*) AS n FROM customer_events "
                "WHERE customer_id = ? AND event_type = 'abandon' AND ts >= ?",
                (customer_id, cutoff),
            )
        return rs.rows[0]["n"] if rs.rows else 0

    def hours_since_last_outreach(self, customer_id: str) -> float:
        from app.turso import get_client

        with get_client() as client:
            rs = client.execute(
                "SELECT ts FROM customer_events WHERE customer_id = ? AND event_type = 'outreach' "
                "ORDER BY ts DESC LIMIT 1",
                (customer_id,),
            )
        if not rs.rows:
            return 999.0
        last = datetime.fromisoformat(rs.rows[0]["ts"])
        return (datetime.now(UTC) - last).total_seconds() / 3600
