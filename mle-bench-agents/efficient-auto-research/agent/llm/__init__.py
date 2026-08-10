"""LLM API: thin wrapper around OpenAI-compatible endpoints.

All provider-specific concerns (model rewriting, reasoning effort, retries
against the upstream, timeouts, token usage logging) live in the repository
relay (`BenchmarkAdapters/LLMRelay/server.py`). Point OPENAI_BASE_URL at it.
"""

from __future__ import annotations

import logging
import os
import time

from openai import OpenAI

logger = logging.getLogger("AutoResearch")

_client: OpenAI | None = None


def get_client() -> OpenAI:
    """Get or create the OpenAI client (singleton)."""
    global _client
    if _client is None:
        # High-effort reasoning calls can exceed the SDK default 600s read
        # timeout; default to unlimited (the relay proxy owns upstream limits).
        timeout_env = os.environ.get("LLM_CLIENT_TIMEOUT", "").strip()
        _client = OpenAI(
            api_key=os.environ.get("OPENAI_API_KEY", ""),
            base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            timeout=float(timeout_env) if timeout_env else None,
        )
    return _client


def query(
    system_message: str,
    user_message: str,
    model: str | None = None,
    temperature: float = 1.0,
) -> tuple[str, int, int]:
    """
    Call LLM and return (response_text, input_tokens, output_tokens).

    Retries up to 3 times on failure with backoff. Upstream-level retries,
    timeouts and token accounting are handled by the relay proxy.
    """
    model = model or "gpt-5.5"
    client = get_client()

    messages = []
    if system_message:
        messages.append({"role": "system", "content": system_message})
    messages.append({"role": "user", "content": user_message})

    for attempt in range(3):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
            )
            text = resp.choices[0].message.content or ""
            usage = resp.usage
            in_tokens = usage.prompt_tokens if usage else 0
            out_tokens = usage.completion_tokens if usage else 0
            return text, in_tokens, out_tokens
        except Exception as e:
            logger.warning(f"LLM call failed (attempt {attempt + 1}/3): {e}")
            if attempt < 2:
                time.sleep(3 * (attempt + 1))
            else:
                raise
