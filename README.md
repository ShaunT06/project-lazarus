# Project Lazarus

**AI-powered revenue recovery agent** — Razorpay Buildathon, Track 03.

Detects revenue at risk (payment drop-offs, checkout abandonment, failed subscriptions),
diagnoses the cause deterministically, negotiates the recovery *within merchant-defined
bounds* using an LLM, and resumes the customer's exact checkout state via a deep link —
with every decision logged and every LLM action re-validated against a strategy config
it cannot override.

> The LLM is a **negotiator inside a fence**, not a decision-maker. The fence — discount
> caps, retry limits, cooldowns — is deterministic code the merchant configures. Every
> proposed tool call is checked against it before anything executes.

## Status

🚧 In development. See [`docs/plan.md`](docs/plan.md) for the full architecture and timeline.

## What is live vs. simulated

This table is the honest accounting. It is updated as components land — nothing here is
aspirational.

| Component | Status |
|---|---|
| Razorpay webhook receipt + signature verification + idempotency | Done — SQLite-backed dedupe, survives restart |
| Razorpay test-mode orders | Not started |
| State serialization / checkout hydration | Not started |
| Error diagnosis engine | Done — deterministic, unmapped codes fall to `unknown` |
| Strategy config engine | Done — hard_stops → rules → defaults |
| Decision agent (OpenRouter) | Done — capped reject-and-correct loop |
| Policy gate | Done — action/discount/retry/cooldown + message-body scan |
| Webhook → agent pipeline wiring | Done — `payment.failed` only; checkout abandonment needs separate tracking (not built) |
| Customer history store (LTV, abandon count, cooldown) | Done, SQLite — LTV/opt-in are placeholders pending real CRM backfill |
| WhatsApp delivery | Not started — `console` channel is the default |
| B2B receivables records | Synthetic by design (see plan §8) |

## Quick start

```bash
python -m venv .venv && .venv/Scripts/activate
pip install -e ".[dev]"
cp .env.example .env   # then fill in your keys
uvicorn app.main:app --reload
```

Redis is optional. The default state store is SQLite so the project runs from a clean
clone with no external services.

## Repo conventions

- One PR per feature, squash-merged into `main`. `main` stays green.
- Conventional commits (`feat:`, `fix:`, `test:`, `docs:`, `chore:`).
- Secrets live in `.env` (gitignored). `.env.example` documents the shape.
- CI runs `ruff` + `pytest` on every PR.

## License

MIT
