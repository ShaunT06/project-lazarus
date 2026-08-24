"""Thin client for OpenRouter's OpenAI-compatible chat/completions endpoint.

Deliberately not the Anthropic SDK / Messages API - OpenRouter's wire format
is OpenAI-style regardless of which underlying model is routed to, per
plan.md section 3 (model swap should be a config change, not a rewrite).
"""

import json
import time
from dataclasses import dataclass, field
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


class OpenRouterClient:
    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float = 60.0,
        max_tokens: int = 1024,
    ):
        self._api_key = api_key or settings.openrouter_api_key
        self._model = model or settings.openrouter_model
        self._max_tokens = max_tokens
        self._client = httpx.Client(base_url=settings.openrouter_base_url, timeout=timeout)

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_retries: int = 5,
    ) -> LLMResponse:
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
        # whole batch run on one flaky call.
        data: dict[str, Any] = {}
        for attempt in range(max_retries + 1):
            resp = self._client.post("/chat/completions", headers=headers, json=body)

            if resp.status_code == 429:
                if attempt == max_retries:
                    resp.raise_for_status()
                retry_after = float(resp.headers.get("Retry-After", 2 * (attempt + 1)))
                time.sleep(retry_after)
                continue

            resp.raise_for_status()
            data = resp.json()
            if "choices" in data:
                break
            if attempt == max_retries:
                raise RuntimeError(f"OpenRouter response missing 'choices': {data}")
            time.sleep(2 * (attempt + 1))

        message = data["choices"][0]["message"]

        tool_calls: list[ToolCall] = []
        for tc in message.get("tool_calls") or []:
            # Always parse via json.loads - never string-match raw tool args.
            args = json.loads(tc["function"]["arguments"] or "{}")
            tool_calls.append(ToolCall(id=tc["id"], name=tc["function"]["name"], arguments=args))

        return LLMResponse(
            content=message.get("content"), tool_calls=tool_calls, raw_message=message
        )

    def close(self) -> None:
        self._client.close()
