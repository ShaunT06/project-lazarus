# Pitch Script — Project Lazarus (5 minutes)

Structure per `plan.md` §9: headline number first, one guardrail-rejection shown live, then
architecture. Numbers below are filled in from the final completed run of
`data/metrics_report.md` (50/50 cases resolved, zero errors, model: `z-ai/glm-5.3-flash`) —
re-run `scripts/generate_report.py` and update this file if the batch is ever re-run and the
numbers shift.

---

## 0:00–0:45 — The headline number

> "We built an agent that recovers revenue Razorpay merchants are currently losing to
> failed payments, abandoned checkouts, and overdue B2B invoices. Here's the number that
> matters: out of a 50-case batch spanning all three failure types, the agent proposed and
> the system approved a recovery action in **76% of cases (38/50)** — entirely autonomously,
> with zero human review of any individual case. Another 4% — 2 cases — were correctly
> blocked from any outreach at all, before the agent was even called. And the B2B receivables
> category — the one where our merchant policy calls for an installment offer instead of a
> plain reminder — hit **15 out of 15**."

Show `data/metrics_report.md`'s headline section on screen. Say the recovery-**attempt** rate
out loud explicitly as attempt rate, not "recovered ₹__" — there's no live customer in this
batch, so a rupee figure would be fiction. Judges who've built payment systems will notice
if you don't self-correct this; self-correcting it here is a credibility signal, not a weakness.

## 0:45–2:15 — The guardrail, shown live (the actual differentiator)

> "Every other 'AI recovery agent' submission you'll see today lets the model decide the
> discount. We don't. Watch what happens when it tries."

Two real events from the completed batch, not staged for the demo:

1. **A genuine live rejection** — case `batch_sub_008`: the agent proposed calling
   `update_customer_record`, which is not in the allowed action set for the matched rule
   (`transient_decline_any_customer`). The policy gate rejected it with the exact reason on
   the spot (`'update_customer_record' is not in allowed_actions for rule
   'transient_decline_any_customer'`) — pull this straight from `data/audit.jsonl`
   (`tool_rejected` event for that case) and read the reason aloud. This is the agent
   genuinely trying something outside its bounds and getting caught in code, not a
   pre-check blocking it before it starts.
2. **A real hard-stop from the batch** — `batch_sub_017` or `batch_checkout_005` in
   `data/batch_results.json` (`outcome: "hard_stop"`) — a repeat abandoner and an opted-out
   customer, where the strategy engine blocked outreach *before the LLM was ever called*.
   Say explicitly: "the agent never even got a chance to overreach here — the fence closed
   before it opened."

Backup if you want a second, on-screen live rejection: run
`pytest tests/test_agent_gate_loop.py::test_repeated_out_of_bounds_calls_hit_gate_exhausted -v`
and narrate as it runs — the test scripts the agent proposing a 50% discount on a case capped
at 0%, four times in a row, and shows the gate rejecting every one before falling back to a
safe default message, logged as `gate_exhausted`. Only needed as a second example; the batch
already has one real rejection to lead with.

> "The LLM never sees a raw discount authority — only the bounds a merchant already approved.
> And even inside those bounds, every single proposed action is re-checked in code, not in
> the prompt. A prompt injection from a customer message literally cannot widen what this
> agent is allowed to do."

## 2:15–4:00 — Architecture, in one breath per box

Walk `docs/plan.md`'s architecture diagram (or the Mermaid version — see below) top to bottom:

1. Razorpay webhook → signature verified, deduped (survives a restart — SQLite-backed, not
   in-memory)
2. Deterministic diagnosis + strategy engine — no LLM, no cost, no hallucination risk, on
   the part of the system that decides *what's allowed*
3. The agent — OpenRouter, model-agnostic by design (currently `z-ai/glm-5.3-flash`, a small
   cheap model — swapping models is a config change, not a rewrite), decides *which* allowed
   action and *how* to phrase it
4. The policy gate — re-validates every proposal: action allowed, discount within cap, retry
   count within limit, cooldown respected, **and the message text itself scanned** so the
   agent can't just *say* a number it didn't get approved
5. Audit trail — every diagnosis, strategy decision, LLM turn, and gate verdict logged to
   JSONL, end to end

> "Here's the line worth remembering: the LLM decides *which* tool and *how to say it* —
> never *how much* or *how many times*. That's the fence."

## 4:00–4:45 — What's real vs. modeled (the honesty slide)

Show the README's status table directly, don't paraphrase it into something rosier.

> "We're not going to pretend everything here is production. Checkout-abandonment orders are
> real Razorpay test-mode orders, genuinely left unpaid. Subscription-failure orders are real
> too, but the failure event is modeled — Razorpay's test mode has no server-only way to force
> a card decline outside a real checkout flow. B2B receivables are fully synthetic by design.
> We'd rather tell you that now than have you find it in the code."

## 4:45–5:00 — Close

> "The bet we made: the interesting engineering problem in an AI agent handling money isn't
> making the model smarter — it's making sure it can't do anything you didn't explicitly
> allow, and proving that on every single call. That's Project Lazarus."

---

## Mermaid architecture diagram (for slides or the README)

```mermaid
flowchart TD
    A[Razorpay Webhook] -->|HMAC verified, deduped| B[Webhook Receiver]
    B --> C[Diagnosis Engine<br/>deterministic]
    C --> D[Strategy Engine<br/>deterministic]
    D -->|allowed actions + bounds only| E[Decision Agent<br/>OpenRouter LLM]
    E -->|proposed tool call| F{Policy Gate<br/>deterministic}
    F -->|approved| G[Tool Executor]
    F -->|rejected + reason| E
    F -->|cap exceeded| H[Safe Default Action]
    G --> I[Audit Trail]
    H --> I
    D -->|hard stop| I
```

## Recording checklist

- [ ] Have `batch_sub_008`'s `tool_rejected` event pulled up in `data/audit.jsonl` beforehand (the live rejection)
- [ ] Have `batch_sub_017` or `batch_checkout_005` looked up in `data/batch_results.json` beforehand (the hard-stop)
- [ ] Optionally have `pytest tests/test_agent_gate_loop.py -v` ready as a second/backup rejection demo
- [ ] Have `data/metrics_report.md` and `README.md`'s status table open in separate tabs
- [ ] Time yourself once, unscripted, before the real take — 5 minutes goes fast
