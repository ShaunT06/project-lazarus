"""Loads the merchant's dialogue policy - the call-time equivalent of
app/strategy_store.py loading config/strategy.example.json. Same reasoning
as lazarusV2's channels/voice/policy.py: the strategy engine still owns the
numbers (max_discount_pct, allowed_actions); this only shapes persona,
style, never-say words, and the closed lever menu's wording.

JSON, not YAML, deliberately - every other config file in this project
(config/strategy.example.json) is JSON, and matching that avoids adding a
YAML dependency for something that isn't a phase-1 requirement.
"""

import json
from pathlib import Path
from typing import Any


class DialoguePolicy:
    def __init__(self, raw: dict[str, Any]):
        self.raw = raw

    @property
    def id(self) -> str:
        return self.raw.get("id", "DP_DEFAULT_V1")

    @property
    def language(self) -> str:
        return self.raw.get("language", "en")

    @property
    def persona(self) -> str:
        return self.raw.get("persona", "a recovery agent")

    @property
    def style(self) -> list[str]:
        return self.raw.get("style", [])

    @property
    def never_say(self) -> list[str]:
        return [w.lower() for w in self.raw.get("never_say", [])]

    @property
    def consent_script(self) -> str:
        return self.raw.get("consent_script", "")

    @property
    def quiet_hours(self) -> tuple[int, int]:
        qh = self.raw.get("quiet_hours", {})
        return int(qh.get("start_hour", 9)), int(qh.get("end_hour", 21))

    @property
    def levers(self) -> dict[str, Any]:
        return self.raw.get("levers", {})

    def pitch(self, key: str, **fmt: Any) -> str:
        template = self.raw.get("pitch", {}).get(key, "")
        try:
            return template.format(**fmt)
        except (KeyError, IndexError):
            return template


def load_dialogue_policy(path: Path) -> DialoguePolicy:
    return DialoguePolicy(json.loads(Path(path).read_text(encoding="utf-8")))
