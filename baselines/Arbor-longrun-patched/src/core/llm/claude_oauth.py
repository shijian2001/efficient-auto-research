"""Claude-subscription Messages provider (experimental).

Authenticates with an OAuth access token obtained via ``arbor login claude`` and
talks to the Anthropic Messages API as a ``Bearer`` token (with the
``anthropic-beta: oauth-2025-04-20`` header) instead of a pay-per-token
``ANTHROPIC_API_KEY``. Everything else — message conversion, thinking replay,
tool calls, prompt caching, parsing — is inherited from
:class:`ClaudeProvider`.

The subscription backend only accepts requests that identify as Claude Code, so
the Claude Code system prompt is prepended to every call. Tokens are refreshed
transparently before each call. Using a Claude subscription token with
third-party tooling may violate Anthropic's terms; this backend is strictly
opt-in.
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator

import anthropic

from ..oauth import anthropic as oauth
from .base import LLMResponse, StreamEvent
from .claude import ClaudeProvider


class ClaudeOAuthProvider(ClaudeProvider):
    """Messages provider backed by a Claude subscription OAuth token."""

    def __init__(
        self,
        model: str = "claude-sonnet-4-5-20250929",
        *,
        max_retries: int = 3,
        timeout: float = 300.0,
        reasoning_effort: str | None = "high",
        thinking_budget_tokens: int | None = None,
        **_ignored: Any,
    ) -> None:
        tokens = oauth.get_valid_tokens()
        self._max_retries = max_retries
        self._access_token = tokens.access_token
        # Build the base provider with a placeholder key (we never use it) so we
        # inherit the tokenizer/config setup, then swap in an OAuth client.
        super().__init__(
            model=model,
            api_key="oauth-placeholder",
            base_url=None,
            max_retries=max_retries,
            timeout=timeout,
            reasoning_effort=reasoning_effort,
            thinking_budget_tokens=thinking_budget_tokens,
        )
        self._rebuild_client(tokens.access_token)

    def _rebuild_client(self, access_token: str) -> None:
        # ``auth_token`` makes the SDK send ``Authorization: Bearer <token>``
        # (and skip ``x-api-key``); the beta header opts into the OAuth backend.
        self._client = anthropic.AsyncAnthropic(
            auth_token=access_token,
            max_retries=self._max_retries,
            timeout=self.timeout,
            default_headers={"anthropic-beta": oauth.OAUTH_BETA},
        )

    async def _ensure_fresh(self) -> None:
        """Refresh the token if it is near expiry, rebuilding the client once."""
        tokens = await asyncio.to_thread(oauth.get_valid_tokens)
        if tokens.access_token != self._access_token:
            self._access_token = tokens.access_token
            self._rebuild_client(tokens.access_token)

    def _build_params(
        self,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        max_tokens: int,
    ) -> dict[str, Any]:
        params = super()._build_params(system, messages, tools, max_tokens)
        # The subscription backend rejects requests whose first system block is
        # not the Claude Code identity, so prepend it ahead of the real prompt.
        params["system"] = [
            {"type": "text", "text": oauth.CLAUDE_CODE_SYSTEM_PROMPT},
            *params["system"],
        ]
        return params

    async def create(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 16384,
    ) -> LLMResponse:
        await self._ensure_fresh()
        return await super().create(
            system=system, messages=messages, tools=tools, max_tokens=max_tokens
        )

    async def create_streaming(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 16384,
    ) -> AsyncIterator[StreamEvent]:
        await self._ensure_fresh()
        async for event in super().create_streaming(
            system=system, messages=messages, tools=tools, max_tokens=max_tokens
        ):
            yield event
