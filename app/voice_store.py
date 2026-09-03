"""Persists voice call sessions for the dashboard's "Voice calls" tab -
the call-channel counterpart to app/conversation_store.py. The live
CallSession (app/voice/session.py) is the source of truth while a call is
in progress; this is what survives across requests and what the dashboard
reads, mirroring the SQLite-local / Turso-deployed split every other store
in this project already uses (see app/stores.py).

Unlike ConversationStore's create-then-update_state split, save() upserts
the whole row every time - a CallSession is small (one call's transcript),
so there's no cost to writing it whole after every turn.
"""

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class VoiceCallStore:
    def __init__(self, db_path: Path):
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS voice_calls (
                    call_id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    outcome TEXT,
                    ended INTEGER NOT NULL DEFAULT 0,
                    committed INTEGER NOT NULL DEFAULT 0,
                    transcript TEXT NOT NULL DEFAULT '[]',
                    reconciliation TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_voice_calls_case ON voice_calls(case_id)")

    def save(self, public: dict[str, Any]) -> None:
        now = datetime.now(UTC).isoformat()
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT created_at FROM voice_calls WHERE call_id = ?", (public["call_id"],)
            ).fetchone()
            created_at = existing["created_at"] if existing else now
            conn.execute(
                """
                INSERT INTO voice_calls
                    (call_id, case_id, state, outcome, ended, committed, transcript,
                     reconciliation, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(call_id) DO UPDATE SET
                    state = excluded.state, outcome = excluded.outcome,
                    ended = excluded.ended, committed = excluded.committed,
                    transcript = excluded.transcript, reconciliation = excluded.reconciliation,
                    updated_at = excluded.updated_at
                """,
                (
                    public["call_id"],
                    public["case_id"],
                    public["state"],
                    public["outcome"],
                    int(public["ended"]),
                    int(public["committed"]),
                    json.dumps(public["transcript"], ensure_ascii=False),
                    json.dumps(public["reconciliation"], ensure_ascii=False)
                    if public["reconciliation"] is not None
                    else None,
                    created_at,
                    now,
                ),
            )

    def get(self, call_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM voice_calls WHERE call_id = ?", (call_id,)).fetchone()
        if row is None:
            return None
        return _row_to_dict(row)

    def list_calls(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM voice_calls ORDER BY updated_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [_row_to_dict(r) for r in rows]


def _row_to_dict(row: Any) -> dict[str, Any]:
    return {
        "call_id": row["call_id"],
        "case_id": row["case_id"],
        "state": row["state"],
        "outcome": row["outcome"],
        "ended": bool(row["ended"]),
        "committed": bool(row["committed"]),
        "transcript": json.loads(row["transcript"]),
        "reconciliation": json.loads(row["reconciliation"]) if row["reconciliation"] else None,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


class TursoVoiceCallStore:
    """Same interface as VoiceCallStore, backed by Turso (libSQL is a
    SQLite-compatible dialect, so the SQL itself is unchanged)."""

    def save(self, public: dict[str, Any]) -> None:
        from app.turso import get_client

        now = datetime.now(UTC).isoformat()
        with get_client() as client:
            existing = client.execute(
                "SELECT created_at FROM voice_calls WHERE call_id = ?", (public["call_id"],)
            )
            created_at = existing.rows[0]["created_at"] if existing.rows else now
            client.execute(
                """
                INSERT INTO voice_calls
                    (call_id, case_id, state, outcome, ended, committed, transcript,
                     reconciliation, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(call_id) DO UPDATE SET
                    state = excluded.state, outcome = excluded.outcome,
                    ended = excluded.ended, committed = excluded.committed,
                    transcript = excluded.transcript, reconciliation = excluded.reconciliation,
                    updated_at = excluded.updated_at
                """,
                (
                    public["call_id"],
                    public["case_id"],
                    public["state"],
                    public["outcome"],
                    int(public["ended"]),
                    int(public["committed"]),
                    json.dumps(public["transcript"], ensure_ascii=False),
                    json.dumps(public["reconciliation"], ensure_ascii=False)
                    if public["reconciliation"] is not None
                    else None,
                    created_at,
                    now,
                ),
            )

    def get(self, call_id: str) -> dict[str, Any] | None:
        from app.turso import get_client

        with get_client() as client:
            rs = client.execute("SELECT * FROM voice_calls WHERE call_id = ?", (call_id,))
        if not rs.rows:
            return None
        return _row_to_dict(rs.rows[0])

    def list_calls(self, limit: int = 100) -> list[dict[str, Any]]:
        from app.turso import get_client

        with get_client() as client:
            rs = client.execute(
                "SELECT * FROM voice_calls ORDER BY updated_at DESC LIMIT ?", (limit,)
            )
        return [_row_to_dict(r) for r in rs.rows]
