"""Zero-config alphaXiv search backend for the SearchAgent.

These two tools are drop-in replacements for ``WebSearchTool`` / ``WebVisitTool``
that talk to the **public** alphaXiv API (no API key, no self-hosted endpoint)
via the ``alphaxiv`` SDK instead of the BrowseComp HTTP shim. They keep the same
``name`` / ``input_schema`` / ``description`` so the SearchAgent prompt and ReAct
loop are unchanged — only the data source differs.

Design: we subclass the HTTP tools so the pure-sync candidate pipeline
(``_extract_candidates`` / ``_merge_candidates`` / ``_select_candidates`` /
``_format_results``) and the visit helpers (``_truncate_tokens`` /
``_format_block``) are reused verbatim. Only the transport is overridden — and
it is overridden as a *native-async* path (no ``requests``, no
``asyncio.to_thread``), because the alphaXiv SDK is async.

``alphaxiv-py`` requires Python >= 3.12, so it is declared as a
marker-gated dependency: bundled with arbor on 3.12+ and absent on 3.10/3.11.
It is imported lazily by :func:`_load_axv` so the module loads either way.
"""

from __future__ import annotations

import re
from typing import Any

from ..base import Tool
from .search import WebSearchTool
from .visit import WebVisitTool

_ABS_URL = "https://www.alphaxiv.org/abs/{}"
# Parse a paper id back out of an alphaXiv abs URL so web_visit can fetch it.
_ABS_RE = re.compile(r"alphaxiv\.org/abs/([^/?#]+)", re.IGNORECASE)
_MISSING_PKG_MSG = (
    "The alphaXiv search backend needs the 'alphaxiv-py' package, which "
    "requires Python >= 3.12. It ships with arbor by default on 3.12+; your "
    "interpreter is older. Upgrade to Python >= 3.12, or install it manually "
    "with:  pip install 'alphaxiv-py>=0.6.0'"
)


def _load_axv():
    """Lazily import the alphaXiv SDK. Raises an actionable RuntimeError if the
    package is unavailable (i.e. running on Python < 3.12)."""
    try:
        from alphaxiv import AlphaXivClient  # type: ignore
        from alphaxiv.exceptions import AlphaXivError  # type: ignore
    except ImportError as exc:  # pragma: no cover - exercised via monkeypatch
        raise RuntimeError(_MISSING_PKG_MSG) from exc
    return AlphaXivClient, AlphaXivError


def _paper_id(url: str) -> str | None:
    m = _ABS_RE.search(url or "")
    return m.group(1) if m else None


def papers_to_items(papers: Any) -> list[dict]:
    """Map alphaXiv paper objects onto the WebSearchTool item contract
    ``{url, title, snippets}``. Shared by AlphaXivSearchTool and AlphaXivBackend."""
    items: list[dict] = []
    for r in papers or []:
        pid = getattr(r, "canonical_id", None) or getattr(
            r, "universal_paper_id", None
        )
        if not pid:
            continue
        title = (getattr(r, "title", None) or "No Title").strip()
        abstract = (getattr(r, "abstract", None) or "").strip()
        authors = getattr(r, "authors", None) or []
        names = ", ".join(
            getattr(a, "display_name", "") for a in authors
        ).strip(", ")
        date = getattr(r, "publication_date", None) or ""
        prefix = " · ".join(p for p in (names, str(date)) if p)
        snippet = f"{prefix} — {abstract}" if prefix else abstract
        items.append(
            {"url": _ABS_URL.format(pid), "title": title, "snippets": snippet}
        )
    return items


class AlphaXivSearchTool(WebSearchTool):
    """``web_search`` backed by the public alphaXiv paper search."""

    def __init__(
        self,
        *,
        cwd: str,
        max_results_per_query: int = 10,
        **kwargs: Any,
    ):
        # Deliberately bypass WebSearchTool.__init__ (it requires endpoint_url).
        Tool.__init__(self, cwd=cwd, **kwargs)
        self._provider = "alphaxiv"
        self._max_results_per_query = max_results_per_query

    async def execute(self, **kwargs: Any) -> str:
        from ._coerce import coerce_str_list

        try:
            queries = coerce_str_list(kwargs.get("query"), field_name="query")
        except ValueError as exc:
            return f"[WebSearchTool] {exc}"

        try:
            AlphaXivClient, AlphaXivError = _load_axv()
        except RuntimeError as exc:
            return f"[search-failed: {exc}]"

        all_candidates: list[dict] = []
        failures: list[str] = []

        try:
            async with AlphaXivClient() as axv:
                for idx, q in enumerate(queries):
                    try:
                        papers = await axv.search.papers_rich(q)
                    except AlphaXivError as exc:
                        failures.append(
                            f"{self._query_label(idx)} failed: {exc}. Query: {q}"
                        )
                        continue
                    items = self._papers_to_items(papers)
                    if not items:
                        failures.append(
                            f"{self._query_label(idx)} returned no results. Query: {q}"
                        )
                        continue
                    all_candidates.extend(
                        self._extract_candidates(q, idx, {"items": items})
                    )
        except Exception as exc:  # noqa: BLE001
            return f"[search-failed: alphaXiv search error: {type(exc).__name__}: {exc}]"

        if not all_candidates and failures:
            return "\n".join(failures)
        if not all_candidates:
            return "No results found. Try a more specific or alternative query."

        merged = self._merge_candidates(all_candidates)
        return self._format_results(queries, merged, failures)

    def _papers_to_items(self, papers: Any) -> list[dict]:
        """Map alphaXiv paper objects onto the WebSearchTool item contract
        ``{url, title, snippets}``."""
        return papers_to_items(papers)


class AlphaXivVisitTool(WebVisitTool):
    """``web_visit`` backed by alphaXiv paper full-text / overview / abstract."""

    def __init__(
        self,
        *,
        cwd: str,
        max_content_tokens: int = 2048,
        **kwargs: Any,
    ):
        # Deliberately bypass WebVisitTool.__init__ (it requires endpoint_url).
        Tool.__init__(self, cwd=cwd, **kwargs)
        self._max_content_tokens = max_content_tokens
        self._summarizer = None
        self._encoding = None  # lazy (used by inherited _truncate_tokens)

    async def execute(self, **kwargs: Any) -> str:
        from ._coerce import coerce_str_list

        try:
            urls = coerce_str_list(
                kwargs.get("url"), field_name="url", extract_urls=True
            )
        except ValueError as exc:
            return f"[WebVisitTool] {exc}"

        goal: str = kwargs.get("goal") or ""

        try:
            AlphaXivClient, AlphaXivError = _load_axv()
        except RuntimeError as exc:
            return f"[search-failed: {exc}]"

        try:
            async with AlphaXivClient() as axv:
                blocks = []
                for url in urls:
                    content = await self._fetch_paper(axv, AlphaXivError, url)
                    blocks.append(self._format_block(url, goal, content))
        except Exception as exc:  # noqa: BLE001
            return f"[search-failed: alphaXiv visit error: {type(exc).__name__}: {exc}]"

        return "\n=======\n".join(blocks).strip()

    async def _fetch_paper(self, axv: Any, AlphaXivError: Any, url: str) -> str:
        pid = _paper_id(url)
        if not pid:
            return (
                "[visit] Not an alphaXiv paper URL. The alphaXiv backend can "
                "only read papers via https://www.alphaxiv.org/abs/<id> links "
                "returned by web_search."
            )
        # Content cascade: full_text > overview > abstract (mirrors ideacheck).
        try:
            ft = await axv.papers.full_text(pid)
            text = getattr(ft, "text", "") or ""
            if text.strip():
                return self._truncate_tokens(text)
        except AlphaXivError:
            pass
        except Exception:  # noqa: BLE001
            pass

        try:
            overview = await axv.papers.overview(pid)
            rendered = self._render_overview(overview)
            if rendered.strip():
                return self._truncate_tokens(rendered)
        except AlphaXivError:
            pass
        except Exception:  # noqa: BLE001
            pass

        try:
            paper = await axv.papers.get(pid)
            abstract = getattr(getattr(paper, "version", None), "abstract", "") or ""
            if abstract.strip():
                return self._truncate_tokens(abstract)
        except AlphaXivError as exc:
            return f"[visit] Could not fetch paper {pid}: {exc}"
        except Exception as exc:  # noqa: BLE001
            return f"[visit] Could not fetch paper {pid}: {type(exc).__name__}: {exc}"

        return f"[visit] No full text, overview, or abstract available for {pid}."

    @staticmethod
    def _render_overview(overview: Any) -> str:
        """Render an alphaXiv AI overview into a plain-text block."""
        s = getattr(overview, "summary", None)
        title = getattr(overview, "title", "") or ""

        def _join(field: str) -> str:
            val = getattr(s, field, None) if s else None
            if not val:
                return ""
            if isinstance(val, (list, tuple)):
                return "\n".join(f"- {x}" for x in val)
            return str(val)

        sections = [
            ("Title", title),
            ("Summary", getattr(s, "summary", "") if s else ""),
            ("Original problem", _join("original_problem")),
            ("Solution", _join("solution")),
            ("Key insights", _join("key_insights")),
            ("Results", _join("results")),
        ]
        parts = [f"{label}:\n{body}" for label, body in sections if str(body).strip()]
        return "\n\n".join(parts)
