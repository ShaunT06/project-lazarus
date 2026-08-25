# Recovery Batch Report

Every number below comes from `data/batch_results.json` and `data/audit.jsonl` - nothing here is hand-picked or fitted after the fact.

## Honest framing

This batch runs the agent end-to-end (diagnosis -> strategy -> agent -> gate -> tool execution) but there is no live customer on the other end - the send_message and generate_payment_link tool calls are real, approved actions, but nobody clicks the link or pays in a batch script. So this report measures the **recovery-attempt rate** (did the agent take an approved, in-bounds action) and the **guardrail's behavior**, not actual rupees recovered or a real time-to-recovery - both would require live customer interaction this project doesn't have. Reporting a rupee figure here would be fiction.

## Headline numbers

- Total cases: **50**
- Resolved with a real outcome: **50** (100.0%)
- Recovery-attempt rate (actioned / resolved): **82.0%**
- Correctly hard-stopped by the guardrail (no outreach sent): **2**
- Gate-exhausted (agent kept proposing out-of-bounds, fell back safely): **0**

## Outcome breakdown

| Outcome | Count | % of total |
|---|---|---|
| actioned | 41 | 82.0% |
| no_action | 7 | 14.0% |
| hard_stop | 2 | 4.0% |

## By category

| Category | actioned | no_action | hard_stop | gate_exhausted | error |
|---|---|---|---|---|---|
| checkout_abandonment | 11 | 3 | 1 | 0 | 0 |
| receivable | 14 | 1 | 0 | 0 | 0 |
| subscription_failure | 16 | 3 | 1 | 0 | 0 |

## Guardrail evidence

- **2 cases** never reached the agent at all - the strategy engine's hard_stops (repeat abandonment, opted-out customer) blocked outreach before any LLM call was made.

- No live reject-and-correct events occurred in this run - every tool call the agent proposed was already within the strategy's bounds. The reject-and-correct path is still verified by `tests/test_agent_gate_loop.py`, which deliberately scripts an out-of-bounds proposal to confirm the gate catches it.


## Honest exception list

- None outstanding - every case resolved.
- "Average time-to-recovery" from the original plan is not reported here: there is no live customer completing a payment in this batch, so there is no real recovery timestamp to measure against. Reporting a synthetic number would misrepresent it as real.
- subscription_failure cases have a real Razorpay order but a modeled failure event (Razorpay's test mode has no server-only way to force a card decline outside the checkout.js/browser flow). checkout_abandonment orders are fully real and genuinely unpaid. receivable records are fully synthetic by design. See README's status table.
