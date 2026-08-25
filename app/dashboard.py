"""API for the merchant/judge-facing dashboard (/dashboard). Two tabs worth
of data, both read-only reflections of state written elsewhere - nothing
here computes a number that isn't traceable back to the audit trail or the
committed batch run:

- Batch tab: the completed 50-case run (data/batch_summary.json, generated
  from data/batch_cases.json + the live run's results - see README/
  metrics_report.md for the honest framing of what these numbers do and
  don't mean).
- Live tab: cases created through the customer chat UI, read straight from
  ConversationStore + AuditLogger as they happen.

Also exposes the strategy config as an editable resource: PUT here writes
through StrategyConfigStore, and the next case (batch or chat) evaluates
against it immediately - no redeploy, since it's the merchant authoring
their own bounds, same fence app/policy_gate.py already enforces.
"""

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException

from app.audit import AuditLogger
from app.conversation_store import ConversationStore, TursoConversationStore
from app.strategy_store import StrategyConfigStore, TursoStrategyConfigStore

_BATCH_SUMMARY_PATH = Path("data/batch_summary.json")


def build_dashboard_router(
    *,
    conversation_store: ConversationStore | TursoConversationStore,
    audit: AuditLogger,
    strategy_store: StrategyConfigStore | TursoStrategyConfigStore,
) -> APIRouter:
    router = APIRouter(prefix="/api/dashboard")

    @router.get("/batch-summary")
    def batch_summary():
        if not _BATCH_SUMMARY_PATH.exists():
            return {"available": False}
        data = json.loads(_BATCH_SUMMARY_PATH.read_text(encoding="utf-8"))
        return {"available": True, **data}

    @router.get("/live-cases")
    def live_cases(limit: int = 200):
        return conversation_store.list_cases(limit=limit)

    @router.get("/live-summary")
    def live_summary():
        cases = conversation_store.list_cases(limit=10_000)
        total = len(cases)
        at_risk = sum(c["cart_amount_inr"] or 0 for c in cases)
        hard_stopped = sum(1 for c in cases if c["hard_stop"])
        gate_exhausted = sum(1 for c in cases if c["gate_exhausted"])

        # Scoped to live (chat-originated) case_ids only - audit.jsonl/audit_log
        # is shared with the webhook pipeline and, locally, with whatever the
        # batch script last wrote, so an unfiltered read would silently mix
        # historical batch runs into "this session"'s numbers.
        live_case_ids = {c["case_id"] for c in cases}
        rejections = 0
        approved = 0
        for case_id in live_case_ids:
            for e in audit.read(case_id=case_id):
                if e["event_type"] == "tool_rejected":
                    rejections += 1
                elif e["event_type"] == "tool_approved_executed":
                    approved += 1

        by_category: dict[str, int] = {}
        for c in cases:
            by_category[c["category"]] = by_category.get(c["category"], 0) + 1

        return {
            "total_live_cases": total,
            "at_risk_inr_total": at_risk,
            "hard_stopped": hard_stopped,
            "gate_exhausted": gate_exhausted,
            "gate_rejections_total": rejections,
            "tool_calls_approved_total": approved,
            "by_category": by_category,
        }

    @router.get("/case/{case_id}/audit")
    def case_audit(case_id: str):
        events = audit.read(case_id=case_id)
        if not events:
            raise HTTPException(status_code=404, detail="no audit events for this case_id")
        return events

    @router.get("/case/{case_id}/transcript")
    def case_transcript(case_id: str):
        convo = conversation_store.get(case_id)
        if convo is None:
            raise HTTPException(status_code=404, detail="unknown case_id")
        return {
            "case_id": case_id,
            "case": convo["case"].model_dump(),
            "strategy": convo["strategy"].model_dump(),
            "hard_stop": convo["hard_stop"],
            "hard_stop_reason": convo["hard_stop_reason"],
            "gate_exhausted": convo["gate_exhausted"],
            "messages": conversation_store.get_display_messages(case_id),
        }

    @router.get("/recent-audit")
    def recent_audit(limit: int = 100):
        """Scoped to live (chat-originated) cases - see the live-summary
        comment for why an unfiltered audit.read() would be misleading here."""
        live_case_ids = {c["case_id"] for c in conversation_store.list_cases(limit=10_000)}
        events: list = []
        for case_id in live_case_ids:
            events.extend(audit.read(case_id=case_id))
        events.sort(key=lambda e: e["ts"])
        return events[-limit:]

    @router.get("/strategy")
    def get_strategy():
        return strategy_store.get()

    @router.put("/strategy")
    def put_strategy(config: dict):
        for key in ("hard_stops", "rules", "defaults"):
            if key not in config:
                raise HTTPException(status_code=400, detail=f"config missing required key: {key}")
        strategy_store.save(config)
        return {"status": "saved"}

    return router
