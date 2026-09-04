# Project Lazarus — original design doc

### AI-Powered Revenue Recovery Agent — Razorpay Buildathon, Track 03

*"Lazarus" — because the whole point of this agent is bringing transactions back from the dead.*

> **Historical.** This is the design written at project kickoff. Some of it changed during
> the build — Redis/WhatsApp were replaced by SQLite-or-Turso and the `/chat` + `/voice` web
> UIs, and the model routed to differs from what's named below. See the root
> [`README.md`](../README.md) for what was actually built and its current status.

---

## 1. One-line pitch

An agent that detects revenue at risk (payment drop-offs, checkout abandonment, failed subscriptions), diagnoses the cause deterministically, negotiates the recovery within merchant-defined bounds using an LLM, and resumes the user's exact checkout state via a WhatsApp deep link — with every decision logged and every LLM action gated against a strategy config it cannot override.

## 2. Why this architecture, in plain terms

Most "AI recovery agent" submissions will let an LLM freely decide discounts and messaging. We don't. The LLM is a **negotiator inside a fence**, not a decision-maker — the fence (discount caps, retry limits, cooldowns) is deterministic code the merchant configures, and every LLM tool call is checked against it before anything executes. This is the part of the pitch worth repeating to judges: **the agent cannot do anything it isn't explicitly allowed to do.**

## 3. Model choice: OpenRouter, not a single locked-in provider

We route the ReAct decision agent's calls through **OpenRouter** rather than a single vendor's API. This is deliberate, not just a convenience:

- **Provider flexibility** — if a model's pricing, latency, or availability shifts mid-build (or mid-production), we swap the model string, not the architecture.
- **Primary model: `anthropic/claude-sonnet-5`** — the strongest balance of reliable tool-calling, instruction-following on bounded prompts, and cost for a per-transaction agent loop (~$2/$10 per M tokens on OpenRouter). This is the model actually doing the negotiation.
- **Fallback / high-volume option: `deepseek/deepseek-v4`** — a much cheaper open-weight model we can route simple/low-ambiguity cases to (e.g. "just send the templated nudge") if we want to demonstrate cost-aware routing as a stretch goal, since OpenRouter makes that a config change, not a rewrite.
- All prompts sent to the model include **only the case context and the pre-approved boundaries** from the strategy engine — never raw discount authority. This is enforced in the prompt *and* re-checked in code by the policy gate, so a prompt-injection attempt from a user message can't widen the model's authority.

## 4. Architecture

```
Payment Drop Event (Razorpay Webhook)
        │
        ▼
Webhook Receiver (FastAPI)
  - Verify X-Razorpay-Signature (HMAC over raw body)
  - Dedupe via x-razorpay-event-id (at-least-once delivery)
  - Return 2xx fast, defer real work to background task
        │
        ├──────────────► Audit Logger (event received, raw payload, timestamp)
        │
        ▼
State Serialization (Infra)
  - Cart items, amount, attempted method, session id
  - Written to Redis, keyed by session_id, TTL 7 days
        │
        ▼
Deterministic Rule Engines (no LLM — instant, free, reliable)
  1. Error Diagnosis Engine
     - Maps Razorpay error_code → cause_category
       (insufficient_funds, card_declined, expired_card, etc.)
  2. Strategy Config Engine
     - Merchant-defined JSON matrix
     - Evaluates customer context: LTV, abandon count, history
     - Outputs: allowed actions + hard bounds
       (e.g. "max 5% discount", "no action — 3rd abandon this week")
        │
        ▼  [Case context + allowed boundaries — nothing more]
ReAct Decision Agent (OpenRouter → anthropic/claude-sonnet-5)
  - Reasons over cause + customer context + allowed bounds
  - Decides WHICH tool to call and HOW to phrase it
  - Does NOT decide discount % or retry count — those come from
    the strategy engine, the LLM only operates inside them
        │
        ▼
Policy Gate (deterministic — code, not prompt instructions)
  - Re-validates every tool call against the strategy config
  - Checks discount caps, retry limits, cooldowns, frequency
  - APPROVE → forward to executor
  - REJECT → reason returned to agent, agent must correct and retry
        │
        ├── REJECT ──► loops back to Decision Agent with reason
        │
        ▼ APPROVE
Tool Executor (real API calls)
  - generate_payment_link()      → Razorpay Orders/Payment Links API
  - generate_split_payment_link() → custom/simulated
  - send_whatsapp_message()      → WhatsApp Cloud API
  - retry_payment()              → Razorpay API
  - update_customer_record()     → internal CRM/DB
        │
        ▼
WhatsApp Link Hydration
  - User clicks deep link
  - Backend fetches serialized state from Redis by session_id
  - Pre-fills Razorpay Checkout — no re-entering cart or re-authenticating
        │
        ▼
Razorpay Checkout (pre-filled) → Payment Success / Recovery Achieved
        │
        ▼
Audit Log (all of the above, end to end)
  - Event logs, diagnosis, strategy output, LLM reasoning,
    every tool call, every gate decision (approved/rejected),
    messages sent, link clicks, amount recovered
```

## 5. What's genuinely AI vs. what's infrastructure

Being upfront about this is a strength, not a weakness — it shows we're not dressing up plumbing as intelligence.

| Component | AI? | Why |
|---|---|---|
| Webhook verification, dedup | No | Deterministic security/reliability logic |
| State serialization to Redis | No | Plain data engineering |
| Error diagnosis (error_code → cause) | No | Already a known, deterministic mapping |
| Strategy config (discount caps, cooldowns) | No | Merchant-authored rules, not learned |
| **Decision agent** (which action, what tone, when to stop within bounds) | **Yes** | Genuine reasoning over ambiguous customer context |
| **Recovery message generation** (incl. Hinglish variants) | **Yes** | Context-aware natural language generation |
| Policy gate | No | Hard-coded validation against config |
| Tool execution | No | Direct API calls |

## 6. Guardrails ("the bar" this track sets)

- **Discount/action caps** — enforced by the strategy config, re-checked by the policy gate, never trusted to the LLM alone
- **Retry limits** — hardcoded max retries per transaction
- **Cooldowns** — no repeated outreach to the same customer within a set window (compliant escalation)
- **Reject-and-correct loop** — when the agent proposes something out of bounds, the gate rejects it with a reason and the agent must retry within limits; this loop itself is a demo-able trust signal
- **Full audit trail** — every diagnosis, strategy decision, LLM reasoning step, tool call, and gate verdict is logged

## 7. Testing strategy

We're treating this like a system to be evaluated, not a demo to be shown once.

1. **Plumbing tests** — resend the same webhook event twice, confirm dedup; kill the server mid-request, confirm Razorpay's retry is handled cleanly.
2. **Diagnosis tests** — since diagnosis is now deterministic, this is a pure unit-test surface: assert every known error_code maps to the correct cause_category.
3. **Decision agent evaluation** — hand-label a held-out batch of cases with the "correct" action before running the agent; report precision/recall on that untouched set, not on cases used while prompt-engineering.
4. **Guardrail behavior tests** — deliberately construct cases designed to hit the discount cap, the retry limit, and the cooldown window; confirm the gate rejects each one and the agent corrects itself instead of failing silently.
5. **End-to-end batch run** — full batch (subscription failures, checkout abandonment, receivables) through the whole pipeline once, untouched, for the headline ₹-recovered number.

## 8. Batch composition (50 records)

| Category | Count | Data source |
|---|---|---|
| Subscription / payment failure | 20 | Real Razorpay test-mode orders + modeled failure events using Razorpay's own error taxonomy |
| Checkout abandonment | 15 | Real Razorpay test-mode orders, left deliberately unpaid |
| Overdue receivables (B2B) | 15 | Synthetic modeled records — flagged explicitly, not a live API |

## 9. Deliverables

- Public repo with the above architecture implemented
- README stating plainly what's live vs. synthetic
- 5-minute pitch video: headline ₹-recovered number first, then one guardrail-rejection case shown live, then architecture
- Metrics report: recovery rate by category, average time-to-recovery, honest exception list

## 10. Timeline

| Dates | Phase |
|---|---|
| Aug 21–23 | Webhook receiver: signature verification, idempotency (done) |
| Aug 24–26 | Batch generation across 3 categories (done) |
| Aug 27 | Redis state serialization + WhatsApp hydration scaffold |
| Aug 28 | Deterministic rule engines: error diagnosis + strategy config |
| Aug 29–30 | ReAct decision agent (OpenRouter) + policy gate + reject/correct loop |
| Aug 31–Sep 1 | Full batch run, metrics, honest exception writeup |
| Sep 2 | README, architecture diagram, pitch video |
| Sep 3 | Buffer |

## 11. Open questions for mentors

- Is `anthropic/claude-sonnet-5` via OpenRouter the right cost/quality tradeoff for the decision agent, or should we route to a cheaper model for low-ambiguity cases?
- Does the reject-and-correct loop need a hard cap on retries too (i.e. what happens if the agent keeps proposing out-of-bounds actions)?
- Any gaps in the strategy config schema before we lock it for the merchant-facing demo?
