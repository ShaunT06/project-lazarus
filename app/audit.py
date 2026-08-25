"""Append-only audit trail. Every diagnosis, strategy decision, LLM turn,
tool call, and gate verdict gets written here - this is the file (or table,
on Postgres) the metrics report, the dashboard, and the "guardrail-rejection
shown live" demo all read from. Nothing is displayed anywhere that didn't
first go through .log().
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class AuditLogger:
    def __init__(self, path: Path):
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, case_id: str, event_type: str, payload: dict[str, Any]) -> None:
        record = {
            "ts": datetime.now(UTC).isoformat(),
            "case_id": case_id,
            "event_type": event_type,
            "payload": payload,
        }
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def read(self, case_id: str | None = None, limit: int = 2000) -> list[dict[str, Any]]:
        """Most-recent-last. Used by the dashboard - read-only, never mutates."""
        if not self._path.exists():
            return []
        records = []
        with self._path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                if case_id is None or record["case_id"] == case_id:
                    records.append(record)
        return records[-limit:]

    def case_ids(self) -> list[str]:
        seen: dict[str, None] = {}
        for record in self.read(limit=100_000):
            seen[record["case_id"]] = None
        return list(seen.keys())


class PostgresAuditLogger:
    """Same interface as AuditLogger, backed by Neon - used when
    settings.database_url is set (Vercel's filesystem is ephemeral, so a
    JSONL file would silently lose every row between invocations)."""

    def __init__(self):
        from app.pg import ensure_schema

        ensure_schema()

    def log(self, case_id: str, event_type: str, payload: dict[str, Any]) -> None:
        from app.pg import get_conn

        with get_conn() as conn:
            conn.execute(
                "INSERT INTO audit_log (ts, case_id, event_type, payload) VALUES (%s, %s, %s, %s)",
                (datetime.now(UTC), case_id, event_type, json.dumps(payload, ensure_ascii=False)),
            )

    def read(self, case_id: str | None = None, limit: int = 2000) -> list[dict[str, Any]]:
        from app.pg import get_conn

        with get_conn() as conn:
            if case_id is None:
                rows = conn.execute(
                    "SELECT ts, case_id, event_type, payload FROM audit_log "
                    "ORDER BY id DESC LIMIT %s",
                    (limit,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT ts, case_id, event_type, payload FROM audit_log "
                    "WHERE case_id = %s ORDER BY id DESC LIMIT %s",
                    (case_id, limit),
                ).fetchall()
        records = [
            {
                "ts": row["ts"].isoformat(),
                "case_id": row["case_id"],
                "event_type": row["event_type"],
                "payload": row["payload"],
            }
            for row in rows
        ]
        records.reverse()
        return records

    def case_ids(self) -> list[str]:
        from app.pg import get_conn

        with get_conn() as conn:
            rows = conn.execute(
                "SELECT DISTINCT case_id FROM audit_log ORDER BY case_id"
            ).fetchall()
        return [row["case_id"] for row in rows]
