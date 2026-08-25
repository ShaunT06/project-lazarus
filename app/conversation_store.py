"""Persists the live, multi-turn chat conversations the customer UI drives.

Two things are stored per case, deliberately kept separate:

- `llm_messages`: the raw OpenAI-style message list fed back into the model
  on every turn (system prompt, tool calls, tool results, etc.) - this is
  agent working state, never shown to the customer directly.
- display messages: the simplified, customer-facing transcript (agent text,
  payment-link cards, system notices) the chat UI actually renders. The
  dashboard's audit view is the technical trail; this is the human one.

Re-entering a case (a customer reply) means: load state, run more turns,
save state back. Nothing here decides anything - agent.py and the policy
gate still own every decision; this is just the state that makes "the
agent remembers the conversation" possible across separate HTTP requests.
"""

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.models import CaseContext, StrategyResult


class ConversationStore:
    """SQLite-backed, for local dev - mirrors PostgresConversationStore."""

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
                """
            )
            conn.execute(
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
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_display_messages_case ON display_messages(case_id)"
            )

    def create(
        self,
        case: CaseContext,
        cause_category: str,
        strategy: StrategyResult,
        llm_messages: list[dict],
    ) -> None:
        now = datetime.now(UTC).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO conversations
                    (case_id, case_context, cause_category, strategy, llm_messages,
                     corrections, approved_discount_pct, gate_exhausted,
                     hard_stop, hard_stop_reason, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 0, 0, 0, ?, ?, ?, ?)
                """,
                (
                    case.case_id,
                    case.model_dump_json(),
                    cause_category,
                    strategy.model_dump_json(),
                    json.dumps(llm_messages, ensure_ascii=False),
                    int(strategy.hard_stop),
                    strategy.hard_stop_reason,
                    now,
                    now,
                ),
            )

    def get(self, case_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM conversations WHERE case_id = ?", (case_id,)
            ).fetchone()
        if row is None:
            return None
        return {
            "case_id": row["case_id"],
            "case": CaseContext.model_validate_json(row["case_context"]),
            "cause_category": row["cause_category"],
            "strategy": StrategyResult.model_validate_json(row["strategy"]),
            "llm_messages": json.loads(row["llm_messages"]),
            "corrections": row["corrections"],
            "approved_discount_pct": row["approved_discount_pct"],
            "gate_exhausted": bool(row["gate_exhausted"]),
            "hard_stop": bool(row["hard_stop"]),
            "hard_stop_reason": row["hard_stop_reason"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def update_state(
        self,
        case_id: str,
        *,
        llm_messages: list[dict],
        corrections: int,
        approved_discount_pct: float,
        gate_exhausted: bool,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE conversations
                SET llm_messages = ?, corrections = ?, approved_discount_pct = ?,
                    gate_exhausted = ?, updated_at = ?
                WHERE case_id = ?
                """,
                (
                    json.dumps(llm_messages, ensure_ascii=False),
                    corrections,
                    approved_discount_pct,
                    int(gate_exhausted),
                    datetime.now(UTC).isoformat(),
                    case_id,
                ),
            )

    def add_display_message(
        self, case_id: str, role: str, kind: str, body: str | None, meta: dict | None = None
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO display_messages (case_id, role, kind, body, meta, ts) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    case_id,
                    role,
                    kind,
                    body,
                    json.dumps(meta or {}, ensure_ascii=False),
                    datetime.now(UTC).isoformat(),
                ),
            )

    def get_display_messages(self, case_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT role, kind, body, meta, ts FROM display_messages "
                "WHERE case_id = ? ORDER BY id ASC",
                (case_id,),
            ).fetchall()
        return [
            {
                "role": r["role"],
                "kind": r["kind"],
                "body": r["body"],
                "meta": json.loads(r["meta"]),
                "ts": r["ts"],
            }
            for r in rows
        ]

    def list_cases(self, limit: int = 200) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT case_id, case_context, cause_category, strategy, gate_exhausted, "
                "hard_stop, hard_stop_reason, created_at, updated_at FROM conversations "
                "ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        out = []
        for r in rows:
            case = json.loads(r["case_context"])
            strategy = json.loads(r["strategy"])
            out.append(
                {
                    "case_id": r["case_id"],
                    "category": case.get("category"),
                    "customer_id": case.get("customer_id"),
                    "cart_amount_inr": case.get("cart_amount_inr"),
                    "cause_category": r["cause_category"],
                    "matched_rule_id": strategy.get("matched_rule_id"),
                    "gate_exhausted": bool(r["gate_exhausted"]),
                    "hard_stop": bool(r["hard_stop"]),
                    "hard_stop_reason": r["hard_stop_reason"],
                    "created_at": r["created_at"],
                    "updated_at": r["updated_at"],
                }
            )
        return out


class PostgresConversationStore:
    """Same interface as ConversationStore, backed by Neon."""

    def __init__(self):
        from app.pg import ensure_schema

        ensure_schema()

    def create(
        self,
        case: CaseContext,
        cause_category: str,
        strategy: StrategyResult,
        llm_messages: list[dict],
    ) -> None:
        from app.pg import get_conn

        now = datetime.now(UTC)
        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO conversations
                    (case_id, case_context, cause_category, strategy, llm_messages,
                     corrections, approved_discount_pct, gate_exhausted,
                     hard_stop, hard_stop_reason, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, 0, 0, FALSE, %s, %s, %s, %s)
                """,
                (
                    case.case_id,
                    case.model_dump_json(),
                    cause_category,
                    strategy.model_dump_json(),
                    json.dumps(llm_messages, ensure_ascii=False),
                    strategy.hard_stop,
                    strategy.hard_stop_reason,
                    now,
                    now,
                ),
            )

    def get(self, case_id: str) -> dict[str, Any] | None:
        from app.pg import get_conn

        with get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM conversations WHERE case_id = %s", (case_id,)
            ).fetchone()
        if row is None:
            return None
        return {
            "case_id": row["case_id"],
            "case": CaseContext.model_validate(row["case_context"]),
            "cause_category": row["cause_category"],
            "strategy": StrategyResult.model_validate(row["strategy"]),
            "llm_messages": row["llm_messages"],
            "corrections": row["corrections"],
            "approved_discount_pct": row["approved_discount_pct"],
            "gate_exhausted": row["gate_exhausted"],
            "hard_stop": row["hard_stop"],
            "hard_stop_reason": row["hard_stop_reason"],
            "created_at": row["created_at"].isoformat(),
            "updated_at": row["updated_at"].isoformat(),
        }

    def update_state(
        self,
        case_id: str,
        *,
        llm_messages: list[dict],
        corrections: int,
        approved_discount_pct: float,
        gate_exhausted: bool,
    ) -> None:
        from app.pg import get_conn

        with get_conn() as conn:
            conn.execute(
                """
                UPDATE conversations
                SET llm_messages = %s, corrections = %s, approved_discount_pct = %s,
                    gate_exhausted = %s, updated_at = %s
                WHERE case_id = %s
                """,
                (
                    json.dumps(llm_messages, ensure_ascii=False),
                    corrections,
                    approved_discount_pct,
                    gate_exhausted,
                    datetime.now(UTC),
                    case_id,
                ),
            )

    def add_display_message(
        self, case_id: str, role: str, kind: str, body: str | None, meta: dict | None = None
    ) -> None:
        from app.pg import get_conn

        with get_conn() as conn:
            conn.execute(
                "INSERT INTO display_messages (case_id, role, kind, body, meta, ts) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (
                    case_id,
                    role,
                    kind,
                    body,
                    json.dumps(meta or {}, ensure_ascii=False),
                    datetime.now(UTC),
                ),
            )

    def get_display_messages(self, case_id: str) -> list[dict[str, Any]]:
        from app.pg import get_conn

        with get_conn() as conn:
            rows = conn.execute(
                "SELECT role, kind, body, meta, ts FROM display_messages "
                "WHERE case_id = %s ORDER BY id ASC",
                (case_id,),
            ).fetchall()
        return [
            {
                "role": r["role"],
                "kind": r["kind"],
                "body": r["body"],
                "meta": r["meta"],
                "ts": r["ts"].isoformat(),
            }
            for r in rows
        ]

    def list_cases(self, limit: int = 200) -> list[dict[str, Any]]:
        from app.pg import get_conn

        with get_conn() as conn:
            rows = conn.execute(
                "SELECT case_id, case_context, cause_category, strategy, gate_exhausted, "
                "hard_stop, hard_stop_reason, created_at, updated_at FROM conversations "
                "ORDER BY updated_at DESC LIMIT %s",
                (limit,),
            ).fetchall()
        out = []
        for r in rows:
            case = r["case_context"]
            strategy = r["strategy"]
            out.append(
                {
                    "case_id": r["case_id"],
                    "category": case.get("category"),
                    "customer_id": case.get("customer_id"),
                    "cart_amount_inr": case.get("cart_amount_inr"),
                    "cause_category": r["cause_category"],
                    "matched_rule_id": strategy.get("matched_rule_id"),
                    "gate_exhausted": r["gate_exhausted"],
                    "hard_stop": r["hard_stop"],
                    "hard_stop_reason": r["hard_stop_reason"],
                    "created_at": r["created_at"].isoformat(),
                    "updated_at": r["updated_at"].isoformat(),
                }
            )
        return out
