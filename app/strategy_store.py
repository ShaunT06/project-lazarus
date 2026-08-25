"""Lets the dashboard edit the merchant's strategy config live and have it
take effect on the very next case - no redeploy, no code change. This is
the same fence app/policy_gate.py enforces; editing it here is the merchant
authoring their own bounds, not the agent or a customer widening them.

Local dev: reads/writes config/strategy.example.json directly, same file
StrategyEngine.from_file already used - no new storage, no behavior change
for the existing CLI scripts and tests.

Vercel deploy: the filesystem is read-only/ephemeral, so edits go to a
Turso table instead, seeded from the same JSON file on first read.
"""

import json
from pathlib import Path
from typing import Any


class StrategyConfigStore:
    def __init__(self, path: Path):
        self._path = path

    def get(self) -> dict[str, Any]:
        return json.loads(self._path.read_text(encoding="utf-8"))

    def save(self, config: dict[str, Any]) -> None:
        self._path.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")


class TursoStrategyConfigStore:
    def __init__(self, seed_path: Path):
        from app.turso import ensure_schema

        ensure_schema()
        self._seed_path = seed_path

    def get(self) -> dict[str, Any]:
        from app.turso import get_client

        with get_client() as client:
            rs = client.execute("SELECT config FROM strategy_config ORDER BY id DESC LIMIT 1")
        if rs.rows:
            return json.loads(rs.rows[0]["config"])
        seed = json.loads(self._seed_path.read_text(encoding="utf-8"))
        self.save(seed)
        return seed

    def save(self, config: dict[str, Any]) -> None:
        from datetime import UTC, datetime

        from app.turso import get_client

        with get_client() as client:
            client.execute(
                "INSERT INTO strategy_config (config, updated_at) VALUES (?, ?)",
                (json.dumps(config, ensure_ascii=False), datetime.now(UTC).isoformat()),
            )
