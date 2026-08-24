"""Runs data/batch_cases.json through diagnosis -> strategy -> agent -> gate,
writes a per-case result file plus a summary, for plan.md's testing
strategy item #5 (the headline recovery-rate run).

HONESTY NOTE: every record in batch_cases.json is currently synthetic/
modeled (is_synthetic=true), including the subscription-failure and
checkout-abandonment categories - plan.md's original design called for
those to come from real Razorpay test-mode orders, but no Razorpay test
keys are configured yet. Swapping in real orders later is a drop-in
replacement of batch_cases.json's contents for those two categories; no
code here needs to change.

Two modes:
  --dry-run   Resolve diagnosis + strategy only. No LLM calls, no API key
              needed, no cost. Good for validating the data and the rule
              matching before spending on a live run.
  (default)   Full pipeline including the OpenRouter agent. Requires
              OPENROUTER_API_KEY in .env.

Usage:
  python scripts/run_batch.py --dry-run
  python scripts/run_batch.py
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agent import run_case  # noqa: E402
from app.audit import AuditLogger  # noqa: E402
from app.config import settings  # noqa: E402
from app.diagnosis import diagnose  # noqa: E402
from app.models import CaseContext  # noqa: E402
from app.openrouter_client import OpenRouterClient  # noqa: E402
from app.strategy import StrategyEngine  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", default="data/batch_cases.json")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve diagnosis + strategy only - no LLM calls, no API key needed.",
    )
    parser.add_argument("--out", default="data/batch_results.json")
    args = parser.parse_args()

    cases_raw = json.loads(Path(args.batch).read_text(encoding="utf-8"))
    engine = StrategyEngine.from_file(settings.strategy_config_path)
    audit = AuditLogger(Path("data/audit.jsonl"))

    client = None
    if not args.dry_run:
        if not settings.openrouter_api_key:
            print(
                "OPENROUTER_API_KEY is not set in .env. Set it, or pass --dry-run "
                "to resolve diagnosis + strategy only (no LLM calls, no cost).",
                file=sys.stderr,
            )
            raise SystemExit(1)
        client = OpenRouterClient()

    results = []
    outcome_counts: Counter = Counter()
    category_counts: Counter = Counter()

    try:
        for raw in cases_raw:
            case = CaseContext(**raw)
            cause_category = diagnose(case.error_code)
            case.extra["cause_category"] = cause_category
            strategy_result = engine.evaluate(case)
            category_counts[case.category] += 1

            if args.dry_run:
                outcome = "hard_stop" if strategy_result.hard_stop else "resolved_no_agent_run"
                results.append(
                    {
                        "case_id": case.case_id,
                        "category": case.category,
                        "cause_category": cause_category,
                        "matched_rule_id": strategy_result.matched_rule_id,
                        "hard_stop": strategy_result.hard_stop,
                        "allowed_actions": strategy_result.allowed_actions,
                        "outcome": outcome,
                    }
                )
                outcome_counts[outcome] += 1
                continue

            run_result = run_case(
                case,
                cause_category,
                strategy_result,
                client,
                audit,
                max_corrections=settings.max_gate_corrections,
                notify_channel=settings.notify_channel,
            )

            if strategy_result.hard_stop:
                outcome = "hard_stop"
            elif run_result.gate_exhausted:
                outcome = "gate_exhausted"
            elif run_result.actions:
                outcome = "actioned"
            else:
                outcome = "no_action"

            results.append(
                {
                    "case_id": case.case_id,
                    "category": case.category,
                    "cause_category": cause_category,
                    "matched_rule_id": strategy_result.matched_rule_id,
                    "outcome": outcome,
                    "actions": run_result.actions,
                    "correction_count": run_result.correction_count,
                }
            )
            outcome_counts[outcome] += 1
    finally:
        if client:
            client.close()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")

    mode = "DRY RUN (diagnosis + strategy only)" if args.dry_run else "LIVE RUN"
    print(f"\n{mode} - {len(cases_raw)} cases")
    print("By category:", dict(category_counts))
    print("By outcome:", dict(outcome_counts))
    print(f"\nFull results: {out_path}")
    print("Full trace: data/audit.jsonl")


if __name__ == "__main__":
    main()
