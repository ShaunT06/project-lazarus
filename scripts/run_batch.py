"""Runs data/batch_cases.json through diagnosis -> strategy -> agent -> gate,
writes a per-case result file plus a summary, for plan.md's testing
strategy item #5 (the headline recovery-rate run).

HONESTY NOTE: checkout_abandonment records are real Razorpay test-mode
orders genuinely left unpaid (is_synthetic: false). subscription_failure
records have a real order but a modeled failure event - Razorpay's test
mode has no server-only way to force a card decline outside the
checkout.js/browser flow (is_synthetic stays true; see
scripts/create_razorpay_orders.py). receivable records are fully
synthetic by design (plan.md section 8).

Two modes:
  --dry-run   Resolve diagnosis + strategy only. No LLM calls, no API key
              needed, no cost. Good for validating the data and the rule
              matching before a live run.
  (default)   Full pipeline including the OpenRouter agent. Requires
              OPENROUTER_API_KEY in .env - free-tier only, per project
              policy (see app/config.py's comment on openrouter_model).

FREE-TIER QUOTA: OpenRouter's free models share a single account-wide cap
(observed: 50 requests/day total across every free model, not per-model -
error body is {"error": {"message": "Rate limit exceeded:
free-models-per-day", ...}}, resets at the UTC day boundary). A live run
is therefore RESUMABLE BY DEFAULT: if --out already has results, any case
whose outcome isn't "error" is kept as-is and skipped, so re-running after
a quota reset only spends new requests on cases that never got a real
result. To force a full fresh run, delete --out first (or pass --no-resume).

If 3 cases in a row come back "error", the run stops early instead of
burning the rest of its local retry budget on a quota that's clearly still
exhausted - already-saved results are untouched, safe to resume later.

Usage:
  python scripts/run_batch.py --dry-run
  python scripts/run_batch.py
  python scripts/run_batch.py --no-resume
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

CONSECUTIVE_ERROR_ABORT_THRESHOLD = 3


def save(results_by_id: dict[str, dict], case_order: list[str], out_path: Path) -> None:
    ordered = [results_by_id[cid] for cid in case_order if cid in results_by_id]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(ordered, indent=2, ensure_ascii=False), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", default="data/batch_cases.json")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve diagnosis + strategy only - no LLM calls, no API key needed.",
    )
    parser.add_argument("--out", default="data/batch_results.json")
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="Ignore any existing --out results and reprocess every case from scratch.",
    )
    args = parser.parse_args()
    out_path = Path(args.out)

    cases_raw = json.loads(Path(args.batch).read_text(encoding="utf-8"))
    case_order = [c["case_id"] for c in cases_raw]
    engine = StrategyEngine.from_file(settings.strategy_config_path)
    audit = AuditLogger(Path("data/audit.jsonl"))

    results_by_id: dict[str, dict] = {}
    # Resume only applies to live runs. --dry-run always reprocesses every
    # case fresh (it's free and fast) - otherwise a stray --dry-run against
    # a partially-completed live results file would silently overwrite
    # error/unattempted cases with fake "resolved" dry-run entries.
    if not args.dry_run and not args.no_resume and out_path.exists():
        for r in json.loads(out_path.read_text(encoding="utf-8")):
            if r["outcome"] != "error":
                results_by_id[r["case_id"]] = r

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

    outcome_counts: Counter = Counter()
    category_counts: Counter = Counter()
    consecutive_errors = 0
    aborted = False

    try:
        for raw in cases_raw:
            case = CaseContext(**raw)
            category_counts[case.category] += 1

            if case.case_id in results_by_id:
                outcome_counts[results_by_id[case.case_id]["outcome"]] += 1
                continue

            cause_category = diagnose(case.error_code)
            case.extra["cause_category"] = cause_category
            strategy_result = engine.evaluate(case)

            if args.dry_run:
                outcome = "hard_stop" if strategy_result.hard_stop else "resolved_no_agent_run"
                results_by_id[case.case_id] = {
                    "case_id": case.case_id,
                    "category": case.category,
                    "cause_category": cause_category,
                    "matched_rule_id": strategy_result.matched_rule_id,
                    "hard_stop": strategy_result.hard_stop,
                    "allowed_actions": strategy_result.allowed_actions,
                    "outcome": outcome,
                }
                outcome_counts[outcome] += 1
                save(results_by_id, case_order, out_path)
                continue

            try:
                run_result = run_case(
                    case,
                    cause_category,
                    strategy_result,
                    client,
                    audit,
                    max_corrections=settings.max_gate_corrections,
                    notify_channel=settings.notify_channel,
                )
            except Exception as exc:  # a flaky free-tier call shouldn't sink the batch
                outcome = "error"
                consecutive_errors += 1
                audit.log(case.case_id, "batch_case_error", {"error": str(exc)})
                results_by_id[case.case_id] = {
                    "case_id": case.case_id,
                    "category": case.category,
                    "cause_category": cause_category,
                    "matched_rule_id": strategy_result.matched_rule_id,
                    "outcome": outcome,
                    "error": str(exc),
                }
                outcome_counts[outcome] += 1
                save(results_by_id, case_order, out_path)
                if consecutive_errors >= CONSECUTIVE_ERROR_ABORT_THRESHOLD:
                    print(
                        f"\n{consecutive_errors} cases in a row failed - stopping early "
                        "(likely still quota-exhausted). Re-run after the reset to resume.",
                        file=sys.stderr,
                    )
                    aborted = True
                    break
                continue

            consecutive_errors = 0
            if strategy_result.hard_stop:
                outcome = "hard_stop"
            elif run_result.gate_exhausted:
                outcome = "gate_exhausted"
            elif run_result.actions:
                outcome = "actioned"
            else:
                outcome = "no_action"

            results_by_id[case.case_id] = {
                "case_id": case.case_id,
                "category": case.category,
                "cause_category": cause_category,
                "matched_rule_id": strategy_result.matched_rule_id,
                "outcome": outcome,
                "actions": run_result.actions,
                "correction_count": run_result.correction_count,
            }
            outcome_counts[outcome] += 1
            save(results_by_id, case_order, out_path)
    finally:
        if client:
            client.close()

    mode = "DRY RUN (diagnosis + strategy only)" if args.dry_run else "LIVE RUN"
    if aborted:
        mode += " (aborted early)"
    print(f"\n{mode} - {len(cases_raw)} cases total, {len(results_by_id)} resolved")
    print("By category:", dict(category_counts))
    print("By outcome:", dict(outcome_counts))
    print(f"\nFull results: {out_path}")
    print("Full trace: data/audit.jsonl")


if __name__ == "__main__":
    main()
