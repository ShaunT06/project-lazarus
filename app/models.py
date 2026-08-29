from typing import Any

from pydantic import BaseModel, Field


class CaseContext(BaseModel):
    """Everything known about one recovery case. This is internal state -
    only a filtered subset (see agent.build_case_summary) ever reaches the LLM.
    """

    case_id: str
    customer_id: str
    customer_ltv_inr: float = 0
    abandons_last_7d: int = 0
    marketing_opt_in: bool = True
    hours_since_last_outreach: float = 999
    error_code: str | None = None
    cart_amount_inr: float = 0
    category: str = "unknown"  # subscription_failure | checkout_abandonment | receivable
    is_synthetic: bool = False
    extra: dict[str, Any] = Field(default_factory=dict)


class StrategyResult(BaseModel):
    matched_rule_id: str
    hard_stop: bool = False
    hard_stop_reason: str | None = None
    allowed_actions: list[str] = Field(default_factory=list)
    max_discount_pct: float = 0
    max_retries: int = 0
    cooldown_hours: float = 0


class GateDecision(BaseModel):
    approved: bool
    reason: str | None = None


class ToolCallRequest(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any]


class RunResult(BaseModel):
    case_id: str
    actions: list[dict[str, Any]] = Field(default_factory=list)
    gate_exhausted: bool = False
    correction_count: int = 0
    cost_usd: float = 0.0
