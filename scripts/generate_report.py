"""Turns data/batch_results.json into the metrics report from plan.md
section 9 (deliverables) and section 7 item 5 (the headline batch run).

HONEST FRAMING: this batch never reaches a real customer - the agent
proposes and the gate approves/executes tool calls (send a message,
generate a payment link), but nobody actually clicks the link or pays,
because there's no live customer on the other end of a batch script.
So this report measures the RECOVERY-ATTEMPT rate (did the agent take an
approved action within bounds) and the guardrail's behavior, not actual
rupees recovered or a real time-to-recovery - those require live
customer interaction this project doesn't have. Reporting a rupee figure
would be fiction; the attempt rate and guardrail behavior are real.

Usage:
  python scripts/generate_report.py
  python scripts/generate_report.py --results data/batch_results.json --out data/metrics_report.md
"""

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path


def load_audit_events(audit_path: Path) -> list[dict]:
    if not audit_path.exists():
        return []
    return [
        json.loads(line)
        for line in audit_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def find_live_gate_rejections(audit_events: list[dict]) -> list[dict]:
    """Rejections that happened *after* the LLM proposed something - i.e.
    the reject-and-correct loop actually firing, not just a hard_stop that
    short-circuits before the agent is ever called. This is the stronger
    demo moment: the agent tried something out of bounds and was corrected."""
    return [e for e in audit_events if e["event_type"] == "tool_rejected"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", default="data/batch_results.json")
    parser.add_argument("--audit", default="data/audit.jsonl")
    parser.add_argument("--out", default="data/metrics_report.md")
    args = parser.parse_args()

    results = json.loads(Path(args.results).read_text(encoding="utf-8"))
    audit_events = load_audit_events(Path(args.audit))
    live_rejections = find_live_gate_rejections(audit_events)

    total = len(results)
    outcome_counts = Counter(r["outcome"] for r in results)
    by_category: dict[str, Counter] = defaultdict(Counter)
    for r in results:
        by_category[r["category"]][r["outcome"]] += 1

    unresolved = total - sum(outcome_counts.values())
    resolved = total - outcome_counts.get("error", 0) - unresolved

    def pct(n: int, d: int) -> str:
        return f"{100 * n / d:.1f}%" if d else "n/a"

    lines = []
    lines.append("# Recovery Batch Report\n")
    lines.append(
        "Every number below comes from `data/batch_results.json` and `data/audit.jsonl` - "
        "nothing here is hand-picked or fitted after the fact.\n"
    )

    lines.append("## Honest framing\n")
    lines.append(
        "This batch runs the agent end-to-end (diagnosis -> strategy -> agent -> gate -> "
        "tool execution) but there is no live customer on the other end - the send_message "
        "and generate_payment_link tool calls are real, approved actions, but nobody clicks "
        "the link or pays in a batch script. So this report measures the **recovery-attempt "
        "rate** (did the agent take an approved, in-bounds action) and the **guardrail's "
        "behavior**, not actual rupees recovered or a real time-to-recovery - both would "
        "require live customer interaction this project doesn't have. Reporting a rupee "
        "figure here would be fiction.\n"
    )

    lines.append("## Headline numbers\n")
    lines.append(f"- Total cases: **{total}**")
    lines.append(f"- Resolved with a real outcome: **{resolved}** ({pct(resolved, total)})")
    if outcome_counts.get("error"):
        lines.append(
            f"- Still errored / unresolved (free-tier quota, retried on next run): "
            f"**{outcome_counts['error'] + unresolved}**"
        )
    lines.append(
        f"- Recovery-attempt rate (actioned / resolved): "
        f"**{pct(outcome_counts.get('actioned', 0), resolved)}**"
    )
    lines.append(
        f"- Correctly hard-stopped by the guardrail (no outreach sent): "
        f"**{outcome_counts.get('hard_stop', 0)}**"
    )
    lines.append(
        f"- Gate-exhausted (agent kept proposing out-of-bounds, fell back safely): "
        f"**{outcome_counts.get('gate_exhausted', 0)}**"
    )
    lines.append("")

    lines.append("## Outcome breakdown\n")
    lines.append("| Outcome | Count | % of total |")
    lines.append("|---|---|---|")
    for outcome in ["actioned", "no_action", "hard_stop", "gate_exhausted", "error"]:
        count = outcome_counts.get(outcome, 0)
        if count:
            lines.append(f"| {outcome} | {count} | {pct(count, total)} |")
    lines.append("")

    lines.append("## By category\n")
    lines.append("| Category | actioned | no_action | hard_stop | gate_exhausted | error |")
    lines.append("|---|---|---|---|---|---|")
    for category, counts in sorted(by_category.items()):
        row = [category] + [
            str(counts.get(o, 0))
            for o in ["actioned", "no_action", "hard_stop", "gate_exhausted", "error"]
        ]
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")

    lines.append("## Guardrail evidence\n")
    lines.append(
        f"- **{outcome_counts.get('hard_stop', 0)} cases** never reached the agent at all - "
        "the strategy engine's hard_stops (repeat abandonment, opted-out customer) blocked "
        "outreach before any LLM call was made.\n"
    )
    if live_rejections:
        lines.append(
            f"- **{len(live_rejections)} live reject-and-correct events** - the agent "
            "proposed a specific tool call, the policy gate rejected it with a reason, and "
            "the loop continued. This is the strongest guardrail demo: the agent genuinely "
            "tried something and was caught in code, not just blocked by a pre-check.\n"
        )
        lines.append("Sample rejection(s):\n")
        for r in live_rejections[:3]:
            lines.append(
                f"- `{r['case_id']}`: `{r['payload']['tool']}` rejected - {r['payload']['reason']}"
            )
    else:
        lines.append(
            "- No live reject-and-correct events occurred in this run - every tool call the "
            "agent proposed was already within the strategy's bounds. The reject-and-correct "
            "path is still verified by `tests/test_agent_gate_loop.py`, which deliberately "
            "scripts an out-of-bounds proposal to confirm the gate catches it.\n"
        )
    lines.append("")

    lines.append("## Honest exception list\n")
    if outcome_counts.get("error"):
        lines.append(
            f"- {outcome_counts['error']} cases hit OpenRouter's free-tier daily request cap "
            "(shared across all free models, not per-model) and did not get a real agent "
            "decision in this run. They are marked `error` in `data/batch_results.json` and "
            "will be retried automatically the next time `scripts/run_batch.py` runs, after "
            "the daily quota resets."
        )
    else:
        lines.append("- None outstanding - every case resolved.")
    lines.append(
        '- "Average time-to-recovery" from the original plan is not reported here: there is '
        "no live customer completing a payment in this batch, so there is no real recovery "
        "timestamp to measure against. Reporting a synthetic number would misrepresent it as "
        "real."
    )
    lines.append(
        "- subscription_failure cases have a real Razorpay order but a modeled failure event "
        "(Razorpay's test mode has no server-only way to force a card decline outside the "
        "checkout.js/browser flow). checkout_abandonment orders are fully real and genuinely "
        "unpaid. receivable records are fully synthetic by design. See README's status table."
    )

    out_path = Path(args.out)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Report written to {out_path}")
    print(
        f"Resolved {resolved}/{total} cases, recovery-attempt rate "
        f"{pct(outcome_counts.get('actioned', 0), resolved)}"
    )


if __name__ == "__main__":
    main()
