"""Thin client for OpenRouter's OpenAI-compatible chat/completions endpoint.

Deliberately not the Anthropic SDK / Messages API - OpenRouter's wire format
is OpenAI-style regardless of which underlying model is routed to, per
plan.md section 3 (model swap should be a config change, not a rewrite).
"""

import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

from app.config import settings


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw_message: dict[str, Any] = field(default_factory=dict)
    cost_usd: float = 0.0


class QuotaExhausted(RuntimeError):
    """The free-tier *daily* cap is spent (X-RateLimit-Remaining: 0) - not a
    transient burst limit. Retrying with backoff cannot help here (the cap
    resets once a day, not in seconds), so the caller should fail fast
    instead of sitting through several rounds of pointless retries."""

    def __init__(self, reset_at: datetime | None):
        self.reset_at = reset_at
        when = reset_at.strftime("%H:%M UTC") if reset_at else "the next daily reset"
        super().__init__(f"OpenRouter free-tier daily quota exhausted, resets at {when}")


class OpenRouterClient:
    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float = 20.0,
        max_tokens: int = 1024,
    ):
        self._api_key = api_key or settings.openrouter_api_key
        self._model = model or settings.openrouter_model
        self._max_tokens = max_tokens
        self._default_timeout = timeout
        self._client = httpx.Client(base_url=settings.openrouter_base_url, timeout=timeout)

    def _sleep_within_deadline(self, seconds: float, deadline: float | None) -> None:
        if deadline is not None:
            seconds = min(seconds, max(0.0, deadline - time.monotonic()))
        if seconds > 0:
            time.sleep(seconds)

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_retries: int = 5,
        deadline: float | None = None,
    ) -> LLMResponse:
        """`deadline`, if given, is a time.monotonic() timestamp bounding the
        ENTIRE call including all retries - not just each individual HTTP
        request. Without this, a single call's own retry loop (attempts x
        per-attempt timeout) can alone consume a caller's whole time budget
        even though no single attempt looks unreasonable - observed live: a
        Vercel-hosted /chat request got hard-killed at exactly 60s with no
        graceful response, traced to one chat() call whose own retries ran
        the full per-turn deadline check never got a chance to re-evaluate."""
        if not self._api_key:
            raise RuntimeError("OPENROUTER_API_KEY is not set")

        # A recovery-negotiation tool call is short - a few sentences plus a
        # small JSON payload. Without an explicit cap, OpenRouter defaults to
        # the model's full output ceiling, which can exceed a free-tier
        # account's affordable balance even though the actual turn is tiny.
        body: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "max_tokens": self._max_tokens,
        }
        if tools:
            body["tools"] = tools

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "X-Title": "Project Lazarus",
        }

        # Free-tier models are both rate-limited harder than paid ones and
        # occasionally return HTTP 200 with a malformed/error body instead of
        # a real completion (a transient upstream provider hiccup, not a
        # client bug) - retry both cases with backoff instead of failing a
        # whole batch run on one flaky call. The one 429 that must NOT be
        # retried is the daily cap (X-RateLimit-Remaining: 0) - it resets
        # once a day, not within a few backoff cycles, so retrying it just
        # makes a chat request hang for ~30s before failing anyway; fail
        # immediately instead, with the actual reset time.
        data: dict[str, Any] = {}
        for attempt in range(max_retries + 1):
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        "OpenRouter call deadline exceeded before a request could be attempted"
                    )
                request_timeout = min(self._default_timeout, remaining)
            else:
                request_timeout = self._default_timeout

            try:
                resp = self._client.post(
                    "/chat/completions", headers=headers, json=body, timeout=request_timeout
                )
            except httpx.TransportError:
                # Connection reset / timeout / DNS blip - transient, not a
                # client bug. Observed: "[WinError 10054] An existing
                # connection was forcibly closed by the remote host".
                if attempt == max_retries:
                    raise
                self._sleep_within_deadline(2 * (attempt + 1), deadline)
                continue

            if resp.status_code == 429:
                if resp.headers.get("X-RateLimit-Remaining") == "0":
                    reset_header = resp.headers.get("X-RateLimit-Reset")
                    reset_at = (
                        datetime.fromtimestamp(int(reset_header) / 1000, tz=UTC)
                        if reset_header
                        else None
                    )
                    raise QuotaExhausted(reset_at)
                if attempt == max_retries:
                    resp.raise_for_status()
                retry_after = float(resp.headers.get("Retry-After", 2 * (attempt + 1)))
                self._sleep_within_deadline(retry_after, deadline)
                continue

            resp.raise_for_status()
            data = resp.json()
            if "choices" in data:
                break
            if attempt == max_retries:
                raise RuntimeError(f"OpenRouter response missing 'choices': {data}")
            self._sleep_within_deadline(2 * (attempt + 1), deadline)

        message = data["choices"][0]["message"]

        tool_calls: list[ToolCall] = []
        for tc in message.get("tool_calls") or []:
            # Always parse via json.loads - never string-match raw tool args.
            args = json.loads(tc["function"]["arguments"] or "{}")
            # A model can emit a JSON array (or any non-object) instead of a
            # proper object here - observed: generate_split_payment_link
            # called with arguments '["installments"]'. Every downstream
            # consumer (the policy gate, the tool executors) assumes a dict
            # and calls .get() on it; coerce to {} rather than crash the
            # whole batch on one malformed call. An empty dict still lets
            # the gate reject the call for missing/invalid fields normally.
            if not isinstance(args, dict):
                args = {}
            tool_calls.append(ToolCall(id=tc["id"], name=tc["function"]["name"], arguments=args))

        return LLMResponse(
            content=message.get("content"),
            tool_calls=tool_calls,
            raw_message=message,
            # On a paid key this is the real per-call spend OpenRouter billed
            # (0.0 on a free model) - surfaced so callers can track and cap
            # cumulative spend rather than trusting an estimate.
            cost_usd=(data.get("usage") or {}).get("cost", 0.0),
        )

    def close(self) -> None:
        self._client.close()
