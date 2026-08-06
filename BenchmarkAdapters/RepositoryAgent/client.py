"""Minimal OpenAI-compatible tool-call client with no third-party dependency."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from ..contracts import AdapterError


@dataclass(frozen=True)
class AssistantResponse:
    message: dict


class OpenAICompatibleClient:
    def __init__(self, *, base_url: str, model: str, proxy: str):
        self.base_url = base_url.rstrip("/")
        self.model = model
        credential = os.environ.get("UPSTREAM_API_KEY") or os.environ.get("OPENAI_API_KEY")
        if not credential:
            raise AdapterError("set UPSTREAM_API_KEY or OPENAI_API_KEY")
        self.credential = credential
        handler = urllib.request.ProxyHandler({"http": proxy, "https": proxy} if proxy else {})
        self.opener = urllib.request.build_opener(handler)

    def complete(self, messages: list[dict], tools: list[dict]) -> AssistantResponse:
        payload = json.dumps(
            {
                "model": self.model,
                "messages": messages,
                "tools": tools,
                "tool_choice": "auto",
                "reasoning_effort": "high",
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=payload,
            headers={
                "Authorization": f"Bearer {self.credential}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        last_error: Exception | None = None
        for attempt in range(4):
            try:
                with self.opener.open(request, timeout=300) as response:
                    data = json.loads(response.read().decode("utf-8"))
                message = data["choices"][0]["message"]
                return AssistantResponse(message=message)
            except (OSError, ValueError, KeyError, urllib.error.HTTPError) as exc:
                last_error = exc
                if attempt < 3:
                    time.sleep(2**attempt)
        raise AdapterError(f"repository Agent LLM request failed: {last_error}")


__all__ = ["AssistantResponse", "OpenAICompatibleClient"]
