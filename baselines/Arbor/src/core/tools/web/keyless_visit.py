"""Keyless ``web_visit`` backends — Jina reader (default) + raw-requests fallback.

The HTTP :class:`WebVisitTool` POSTs to a *separate* browse-API endpoint, so
without ``web_browse_endpoint`` configured there is no way to open a general
URL; the alphaXiv visit tool only opens ``alphaxiv.org/abs/<id>`` papers. This
module fills that gap with a no-key fetcher:

- :class:`JinaVisitTool` — fetches any URL via the Jina reader
  (``https://r.jina.ai/<url>`` → clean markdown, no key required; an optional
  ``JINA_API_KEY`` raises rate limits), falling back to a raw ``requests`` GET
  with light HTML→text cleaning.
- :class:`RoutingVisitTool` — composes an alphaXiv visit tool with a Jina visit
  tool and routes each URL to the right one (paper ids → alphaXiv SDK for full
  text; everything else → Jina), so one ``web_visit`` opens both.

Both reuse :class:`WebVisitTool`'s ``_truncate_tokens`` / ``_format_block`` /
``execute`` so the output contract is identical.
"""

from __future__ import annotations

import os
import re
from typing import Any

import requests

from ..base import Tool
from .visit import WebVisitTool, _fail_response
from .alphaxiv import _paper_id

_JINA_READER_BASE = "https://r.jina.ai/"
_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

_TAG_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t]{3,}")


def _html_to_text(html: str) -> str:
    """Very light HTML → text: drop script/style, strip tags, unescape, collapse."""
    import html as _htmlmod

    text = _TAG_RE.sub(" ", html)
    text = _HTML_TAG_RE.sub(" ", text)
    text = _htmlmod.unescape(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = _WS_RE.sub(" ", text)
    return text.strip()


def _clean_markdown(text: str) -> str:
    """Mirror WebVisitTool's post-fetch cleaning (strip bare URLs, collapse rules)."""
    text = re.sub(r"\(https?:.*?\)|\[https?:.*?\]", "", text)
    text = text.replace("---", "-").replace("===", "=")
    while "   " in text:
        text = text.replace("   ", " ")
    return text


def _pdf_to_text(data: bytes) -> str:
    """Extract text from PDF bytes via pypdf (lazy import; '' if unavailable)."""
    try:
        import io

        from pypdf import PdfReader
    except ImportError:
        return ""
    try:
        reader = PdfReader(io.BytesIO(data))
        return "\n".join((page.extract_text() or "") for page in reader.pages).strip()
    except Exception:  # noqa: BLE001 - malformed/encrypted PDF
        return ""


class JinaVisitTool(WebVisitTool):
    """Keyless ``web_visit`` via the Jina reader, with a raw-requests fallback."""

    def __init__(
        self,
        *,
        cwd: str,
        max_content_tokens: int = 2048,
        timeout: tuple[int, int] = (10, 60),
        jina_api_key: str | None = None,
        use_jina: bool = True,
        **kwargs: Any,
    ):
        # Bypass WebVisitTool.__init__ (it requires a browse endpoint_url).
        Tool.__init__(self, cwd=cwd, **kwargs)
        self._max_content_tokens = max_content_tokens
        self._timeout = timeout
        self._jina_api_key = jina_api_key or os.environ.get("JINA_API_KEY")
        self._use_jina = use_jina
        self._summarizer = None
        self._encoding = None  # lazy (inherited _truncate_tokens)

    # execute() is inherited from WebVisitTool — it calls self._fetch_page in
    # worker threads and formats via self._format_block.

    def _fetch_page(self, url: str) -> str:
        text = self._fetch_via_jina(url) if self._use_jina else ""
        if not text:
            text = self._fetch_via_requests(url)
        if not text:
            return "[visit] Failed to read page (jina + direct fetch both failed)."
        return self._truncate_tokens(_clean_markdown(text))

    def _fetch_via_jina(self, url: str) -> str:
        headers = {
            "Accept": "text/plain",
            "X-Return-Format": "markdown",
            "User-Agent": _BROWSER_UA,
        }
        if self._jina_api_key:
            headers["Authorization"] = f"Bearer {self._jina_api_key}"
        try:
            resp = requests.get(
                _JINA_READER_BASE + url, headers=headers, timeout=self._timeout
            )
            if resp.status_code == 200 and resp.text.strip():
                return resp.text
        except requests.RequestException:
            pass
        return ""

    def _fetch_via_requests(self, url: str) -> str:
        try:
            resp = requests.get(
                url, headers={"User-Agent": _BROWSER_UA}, timeout=self._timeout
            )
            if resp.status_code != 200:
                return ""
            ctype = resp.headers.get("Content-Type", "").lower()
            if "pdf" in ctype or url.lower().split("?")[0].endswith(".pdf"):
                return _pdf_to_text(resp.content)
            if "html" in ctype or not ctype:
                return _html_to_text(resp.text)
            if "text" in ctype or "json" in ctype:
                return resp.text
        except requests.RequestException:
            pass
        return ""


class RoutingVisitTool(Tool):
    """``web_visit`` that routes each URL to the best fetcher.

    alphaXiv paper URLs go to the alphaXiv SDK (full text); everything else
    goes to the keyless Jina/requests fetcher. Output contract is identical to
    :class:`WebVisitTool`.
    """

    name = "web_visit"
    description = WebVisitTool.description
    input_schema = WebVisitTool.input_schema
    is_read_only = True

    def __init__(
        self,
        *,
        cwd: str,
        jina: JinaVisitTool,
        alphaxiv: Tool | None = None,
        **kwargs: Any,
    ):
        super().__init__(cwd=cwd, **kwargs)
        self._jina = jina
        self._alpha = alphaxiv

    async def execute(self, **kwargs: Any) -> str:
        from ._coerce import coerce_str_list

        try:
            urls = coerce_str_list(
                kwargs.get("url"), field_name="url", extract_urls=True
            )
        except ValueError as exc:
            return f"[WebVisitTool] {exc}"
        goal: str = kwargs.get("goal") or ""

        alpha_urls = [u for u in urls if self._alpha is not None and _paper_id(u)]
        other_urls = [u for u in urls if u not in alpha_urls]

        blocks: list[str] = []
        if alpha_urls and self._alpha is not None:
            blocks.append(await self._alpha.execute(url=alpha_urls, goal=goal))
        if other_urls:
            blocks.append(await self._jina.execute(url=other_urls, goal=goal))
        if not blocks:
            return _fail_response(", ".join(urls), goal)
        return "\n=======\n".join(b for b in blocks if b).strip()
