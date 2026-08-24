"""Append-only JSONL audit trail. Every diagnosis, strategy decision, LLM
turn, tool call, and gate verdict gets written here - this is the file the
'metrics report' and 'guardrail-rejection case shown live' come from.
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
