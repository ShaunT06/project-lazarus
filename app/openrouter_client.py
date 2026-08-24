"""Thin client for OpenRouter's OpenAI-compatible chat/completions endpoint.

Deliberately not the Anthropic SDK / Messages API - OpenRouter's wire format
is OpenAI-style regardless of which underlying model is routed to, per
plan.md section 3 (model swap should be a config change, not a rewrite).
"""

import json
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
    def __init__(self, api_key: str | None = None, model: str | None = None, timeout: float = 60.0):
        self._api_key = api_key or settings.openrouter_api_key
        self._model = model or settings.openrouter_model
        self._client = httpx.Client(base_url=settings.openrouter_base_url, timeout=timeout)

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        if not self._api_key:
            raise RuntimeError("OPENROUTER_API_KEY is not set")

        body: dict[str, Any] = {"model": self._model, "messages": messages}
        if tools:
            body["tools"] = tools

        resp = self._client.post(
            "/chat/completions",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "X-Title": "Project Lazarus",
            },
            json=body,
        )
        resp.raise_for_status()
        data = resp.json()
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
