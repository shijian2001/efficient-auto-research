"""WebVisitTool — fetch + clean a webpage and return truncated text.

Note on summarization: BrowseComp's VisitTool ran an LLM ``_summarize_single``
step over the raw page. That coupling is intentionally dropped here so that
``core/tools/`` does not depend on an LLM client. The returned text is the
cleaned (token-truncated) page; the calling agent can summarise it in its next
reasoning step (or, in Phase 2, the SearchAgent can inject a ``summarizer``
callable).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from typing import Any, Awaitable, Callable

import requests

from ..base import Tool

_MAX_VISIT_RETRIES = 3
_BROWSE_API_MAX_TOKENS = 8192  # Token budget asked of the upstream browse API.


class _RetryableVisitError(Exception):
    pass


def _fail_response(url: str, goal: str) -> str:
    return (
        f"The useful information in {url} for user goal {goal} as follows: \n\n"
        "Evidence in page: \n"
        "The provided webpage content could not be accessed. "
        "Please check the URL or file format.\n\n"
        "Summary: \n"
        "The webpage content could not be processed, and therefore, "
        "no information is available.\n\n"
    )


class WebVisitTool(Tool):
    """Fetch one or more URLs and return cleaned, token-truncated content.

    The returned text is intentionally raw (not LLM-summarised) so the calling
    agent — which already has a goal in context — can do the synthesis itself.
    """

    name = "web_visit"
    description = (
        "Visit one or more webpages and return cleaned, truncated content "
        "tagged with the user's goal.\n\n"
        "Pass `url` as either a single URL string or an array of URLs, plus a "
        "`goal` string describing what you are looking for on the page (e.g. "
        "\"determine if this paper proposes the same idea: <hypothesis>\"). "
        "The returned text is the page content (not an LLM summary); reason "
        "over it directly in your next step."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "url": {
                "type": ["string", "array"],
                "items": {"type": "string"},
                "minItems": 1,
                "description": (
                    "The URL(s) of the webpage(s) to visit. "
                    "Can be a single URL or an array of URLs."
                ),
            },
            "goal": {
                "type": "string",
                "description": "What you are trying to learn from the page(s).",
            },
        },
        "required": ["url", "goal"],
    }
    is_read_only = True

    def __init__(
        self,
        *,
        cwd: str,
        endpoint_url: str,
        timeout: tuple[int, int] = (10, 60),
        max_content_tokens: int = 2048,
        api_key: str | None = None,
        summarizer: Callable[[str, str], Awaitable[str]] | None = None,
        **kwargs: Any,
    ):
        super().__init__(cwd=cwd, **kwargs)
        if not endpoint_url:
            raise ValueError("WebVisitTool requires a non-empty endpoint_url.")
        self._endpoint_url = endpoint_url
        self._timeout = timeout
        self._max_content_tokens = max_content_tokens
        self._api_key = api_key
        self._summarizer = summarizer
        self._encoding = None  # lazy

    # ── Async surface ───────────────────────────────────────────────

    async def execute(self, **kwargs: Any) -> str:
        from ._coerce import coerce_str_list

        try:
            urls = coerce_str_list(
                kwargs.get("url"), field_name="url", extract_urls=True
            )
        except ValueError as exc:
            return f"[WebVisitTool] {exc}"

        goal: str = kwargs.get("goal") or ""

        # Fetch all pages in parallel — each in its own worker thread.
        contents: list[str] = await asyncio.gather(
            *(asyncio.to_thread(self._fetch_page, u) for u in urls)
        )

        async def _build_block(url: str, content: str) -> str:
            block = self._format_block(url, goal, content)
            if self._summarizer is not None and not content.startswith("[visit] "):
                try:
                    summary = await self._summarizer(content, goal)
                    if summary:
                        block = (
                            f"The useful information in {url} for user goal {goal} as follows: \n\n"
                            f"Summary: \n{summary}\n\n"
                        )
                except Exception as exc:  # noqa: BLE001
                    block += f"\n[summarizer-failed: {type(exc).__name__}: {exc}]"
            return block

        blocks = await asyncio.gather(
            *(_build_block(u, c) for u, c in zip(urls, contents))
        )
        return "\n=======\n".join(blocks).strip()

    # ── Sync helpers (run in worker threads) ────────────────────────

    def _build_headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self._api_key:
            h["Authorization"] = f"Bearer {self._api_key}"
        return h

    @staticmethod
    def _retry_delay(attempt: int) -> float:
        return min(max(1.0 * (2 ** (attempt - 1)), 2.0), 60.0)

    def _truncate_tokens(self, text: str) -> str:
        if self._encoding is None:
            try:
                import tiktoken
                self._encoding = tiktoken.get_encoding("cl100k_base")
            except Exception:
                self._encoding = False  # sentinel: tiktoken unavailable
        if self._encoding is False:
            # Char-based fallback: ~4 chars/token
            limit = self._max_content_tokens * 4
            return text if len(text) <= limit else text[:limit]
        tokens = self._encoding.encode(text)
        if len(tokens) <= self._max_content_tokens:
            return text
        return self._encoding.decode(tokens[: self._max_content_tokens])

    def _fetch_page(self, url: str) -> str:
        payload = {"url": url, "max_tokens": _BROWSE_API_MAX_TOKENS}
        headers = self._build_headers()
        last_error: Exception | None = None

        for attempt in range(1, _MAX_VISIT_RETRIES + 1):
            try:
                resp = requests.post(
                    self._endpoint_url,
                    json=payload,
                    headers=headers,
                    timeout=self._timeout,
                )
                if resp.status_code == 429:
                    raise _RetryableVisitError(f"Server Error: {resp.status_code}")
                if resp.status_code >= 500:
                    body = resp.text[:200].strip()
                    if body:
                        raise ValueError(f"Browse failed ({resp.status_code}): {body}")
                    raise _RetryableVisitError(f"Server Error: {resp.status_code}")
                if resp.status_code == 422:
                    raise ValueError(f"URL Unprocessable: {url}")

                try:
                    data = resp.json()
                except json.JSONDecodeError as exc:
                    raise _RetryableVisitError(f"Invalid JSON response: {exc}")
                if not data.get("overall_success"):
                    err_msg = data.get("error_message") or "Unknown error"
                    raise _RetryableVisitError(f"Browse API error: {err_msg}")

                result_text = data.get("semanticDocument", "")
                if not isinstance(result_text, str) or not result_text.strip():
                    return "[visit] Empty content."

                text = re.sub(r"\(https?:.*?\)|\[https?:.*?\]", "", result_text)
                text = text.replace("---", "-").replace("===", "=")
                while "   " in text:
                    text = text.replace("   ", " ")
                return self._truncate_tokens(text)
            except ValueError as exc:
                return f"[visit] {exc}"
            except (requests.RequestException, _RetryableVisitError) as exc:
                last_error = exc
                if attempt == _MAX_VISIT_RETRIES:
                    break
                time.sleep(self._retry_delay(attempt))
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                break

        if last_error is None:
            return "[visit] Failed to read page."
        return f"[visit] Failed to read page: {type(last_error).__name__}: {last_error}"

    def _format_block(self, url: str, goal: str, content: str) -> str:
        if (
            not content
            or content.startswith("[visit] ")
        ):
            return _fail_response(url, goal)
        return (
            f"The useful information in {url} for user goal {goal} as follows: \n\n"
            f"Page content (cleaned, ≤{self._max_content_tokens} tokens):\n{content}\n\n"
        )


def web_browse_endpoint_from_env() -> str | None:
    return os.environ.get("WEB_BROWSE_ENDPOINT")
