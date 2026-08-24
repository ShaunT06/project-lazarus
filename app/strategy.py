"""Merchant-authored strategy config engine. No LLM involved.

Evaluation order: hard_stops (any match -> stop, no outreach) -> rules
(first match wins) -> defaults. The decision agent never sees this file -
it only ever receives the resolved StrategyResult (allowed_actions + bounds).
"""

import json
from pathlib import Path
from typing import Any

from app.models import CaseContext, StrategyResult


def _condition_matches(case: dict[str, Any], when: dict[str, Any]) -> bool:
    for key, cond in when.items():
        value = case.get(key)
        if isinstance(cond, list):
            if value not in cond:
                return False
        elif isinstance(cond, dict):
            for op, threshold in cond.items():
                if op == "eq" and value != threshold:
                    return False
                if op == "gte" and not (value is not None and value >= threshold):
                    return False
                if op == "lte" and not (value is not None and value <= threshold):
                    return False
                if op == "lt" and not (value is not None and value < threshold):
                    return False
                if op == "gt" and not (value is not None and value > threshold):
                    return False
        else:
            if value != cond:
                return False
    return True


class StrategyEngine:
    def __init__(self, config: dict[str, Any]):
        self._config = config

    @classmethod
    def from_file(cls, path: Path) -> "StrategyEngine":
        return cls(json.loads(path.read_text(encoding="utf-8")))

    def evaluate(self, case: CaseContext) -> StrategyResult:
        case_dict = case.model_dump()
        case_dict["cause_category"] = case_dict.get("error_code")
        # cause_category is injected by the caller after diagnosis; see agent.py
        if "cause_category" in case.extra:
            case_dict["cause_category"] = case.extra["cause_category"]

        for stop in self._config.get("hard_stops", []):
            if _condition_matches(case_dict, stop["when"]):
                return StrategyResult(
                    matched_rule_id=stop["id"],
                    hard_stop=True,
                    hard_stop_reason=stop["reason"],
                    allowed_actions=[],
                )

        for rule in self._config.get("rules", []):
            if _condition_matches(case_dict, rule["when"]):
                allow = rule["allow"]
                return StrategyResult(
                    matched_rule_id=rule["id"],
                    allowed_actions=allow["allowed_actions"],
                    max_discount_pct=allow["max_discount_pct"],
                    max_retries=allow["max_retries"],
                    cooldown_hours=allow["cooldown_hours"],
                )

        defaults = self._config["defaults"]
        return StrategyResult(
            matched_rule_id="__default__",
            allowed_actions=defaults["allowed_actions"],
            max_discount_pct=defaults["max_discount_pct"],
            max_retries=defaults["max_retries"],
            cooldown_hours=defaults["cooldown_hours"],
        )

    @property
    def global_limits(self) -> dict[str, Any]:
        return self._config.get("global_limits", {})
