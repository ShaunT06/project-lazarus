"""Turns data/batch_results.json + data/batch_cases.json into
data/batch_summary.json, the file app/dashboard.py's "Batch" tab reads
directly. Keeps the dashboard's numbers in sync with data/metrics_report.md
(generated separately by generate_report.py from the same results file) -
run both after any batch re-run.

Usage:
  python scripts/generate_batch_summary.py
"""

import argparse
import json
from collections import defaultdict
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", default="data/batch_results.json")
    parser.add_argument("--cases", default="data/batch_cases.json")
    parser.add_argument("--out", default="data/batch_summary.json")
    args = parser.parse_args()

    results = json.loads(Path(args.results).read_text(encoding="utf-8"))
    cases_by_id = {
        c["case_id"]: c for c in json.loads(Path(args.cases).read_text(encoding="utf-8"))
    }

    total = len(results)
    outcome_counts: dict[str, int] = defaultdict(int)
    by_category: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    at_risk_total = 0.0
    at_risk_actioned = 0.0
    by_category_at_risk: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    case_rows = []

    for r in results:
        case = cases_by_id[r["case_id"]]
        cart = float(case["cart_amount_inr"])
        outcome = r["outcome"]

        outcome_counts[outcome] += 1
        by_category[r["category"]][outcome] += 1
        at_risk_total += cart
        by_category_at_risk[r["category"]]["total"] += cart
        if outcome == "actioned":
            at_risk_actioned += cart
            by_category_at_risk[r["category"]]["actioned"] += cart

        actions = r.get("actions", [])
        case_rows.append(
            {
                "case_id": r["case_id"],
                "category": r["category"],
                "cause_category": r["cause_category"],
                "matched_rule_id": r["matched_rule_id"],
                "outcome": outcome,
                "cart_amount_inr": case["cart_amount_inr"],
                "customer_ltv_inr": case["customer_ltv_inr"],
                "correction_count": r.get("correction_count", 0),
                "num_actions": len(actions),
                "first_action_tool": actions[0]["tool"] if actions else None,
            }
        )

    resolved = total - outcome_counts.get("error", 0)
    recovery_attempt_rate = (
        round(100 * outcome_counts.get("actioned", 0) / resolved, 1) if resolved else 0.0
    )

    summary = {
        "generated_from": "data/batch_cases.json + data/batch_results.json (50-case live run)",
        "total_cases": total,
        "resolved": resolved,
        "outcome_counts": dict(outcome_counts),
        "by_category": {cat: dict(counts) for cat, counts in sorted(by_category.items())},
        "at_risk_inr_total": at_risk_total,
        "at_risk_inr_actioned": at_risk_actioned,
        "by_category_at_risk_inr": {
            cat: dict(amounts) for cat, amounts in sorted(by_category_at_risk.items())
        },
        "recovery_attempt_rate_pct": recovery_attempt_rate,
        "cases": case_rows,
    }

    out_path = Path(args.out)
    out_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"Summary written to {out_path}")
    print(f"Resolved {resolved}/{total}, recovery-attempt rate {recovery_attempt_rate}%")


if __name__ == "__main__":
    main()
