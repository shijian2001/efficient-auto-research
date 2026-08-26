"""WebSearchTool — provider-agnostic batched web search.

Currently backed by the BrowseComp HTTP search endpoint, but the endpoint URL
and provider name are configured via constructor so other backends (Tavily,
Serper, a local index, …) can be plugged in by subclassing or by routing
through a compatible HTTP shim.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import requests

from ..base import Tool

_MAX_SEARCH_RETRIES = 3
_MAX_RETURNED_CANDIDATES = 18
_MAX_SHARED_CANDIDATES = 6
_MAX_SNIPPET_CHARS = 220


class _RetryableSearchError(Exception):
    pass


class WebSearchTool(Tool):
    """Batch web search tool — issues one or more queries and returns a
    compact, deduplicated candidate list.

    Output format is a single text block summarising the search; the agent
    is expected to pick a few URLs and pass them to ``WebVisitTool``.
    """

    name = "web_search"
    description = (
        "Performs batched web searches and returns a compact, deduplicated "
        "list of the strongest candidate URLs across the supplied queries.\n\n"
        "Use this when you want to scan related literature / prior work for a "
        "specific hypothesis. Pass 2-3 distinct queries (different angles) in "
        "a single call rather than searching one query at a time."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "array",
                "items": {"type": "string"},
                "description": "A list of search queries (1-5 recommended).",
            }
        },
        "required": ["query"],
    }
    is_read_only = True

    def __init__(
        self,
        *,
        cwd: str,
        endpoint_url: str | None = None,
        provider: str = "google",
        max_results_per_query: int = 10,
        timeout: tuple[int, int] = (5, 30),
        api_key: str | None = None,
        backends: list[Any] | None = None,
        **kwargs: Any,
    ):
        super().__init__(cwd=cwd, **kwargs)
        self._provider = provider
        self._max_results_per_query = max_results_per_query
        self._timeout = timeout
        self._api_key = api_key
        # New multi-backend path: fan out across SearchBackend adapters.
        # Legacy path: a single self-hosted HTTP endpoint (unchanged behavior).
        self._backends = backends
        if backends is None:
            if not endpoint_url:
                raise ValueError(
                    "WebSearchTool requires a non-empty endpoint_url or a backends list."
                )
            self._endpoint_url = endpoint_url
        elif not backends:
            raise ValueError("WebSearchTool requires at least one backend.")

    # ── Async surface ───────────────────────────────────────────────

    async def execute(self, **kwargs: Any) -> str:
        from ._coerce import coerce_str_list

        try:
            queries = coerce_str_list(kwargs.get("query"), field_name="query")
        except ValueError as exc:
            return f"[WebSearchTool] {exc}"

        if self._backends is not None:
            return await self._run_backends(queries)
        # Wrap the entire batch (including its 0.5s inter-query sleeps) in a
        # single to_thread worker so we don't fan out N blocking workers.
        return await asyncio.to_thread(self._run_sync, queries)

    async def _run_backends(self, queries: list[str]) -> str:
        """Fan out each query across all backends, then merge/format as usual."""
        all_candidates: list[dict] = []
        failures: list[str] = []
        for idx, q in enumerate(queries):
            results = await asyncio.gather(
                *(b.search(q, self._max_results_per_query) for b in self._backends),
                return_exceptions=True,
            )
            got_any = False
            for backend, res in zip(self._backends, results):
                if isinstance(res, Exception):
                    failures.append(
                        f"{self._query_label(idx)} [{backend.name}] failed with "
                        f"{type(res).__name__}: {res}. Query: {q}"
                    )
                    continue
                if res:
                    got_any = True
                    all_candidates.extend(self._extract_candidates(q, idx, {"items": res}))
            if not got_any:
                failures.append(f"{self._query_label(idx)} returned no results. Query: {q}")

        if not all_candidates and failures:
            return "\n".join(failures)
        if not all_candidates:
            return "No results found. Try a more specific or alternative query."
        merged = self._merge_candidates(all_candidates)
        return self._format_results(queries, merged, failures)

    # ── Sync core (runs in a worker thread) ─────────────────────────

    def _run_sync(self, queries: list[str]) -> str:
        all_candidates: list[dict] = []
        failures: list[str] = []

        for idx, q in enumerate(queries):
            if idx > 0:
                time.sleep(0.5)
            try:
                results = self._request_results(q)
                if not results.get("items"):
                    failures.append(
                        f"{self._query_label(idx)} returned no results. Query: {q}"
                    )
                    continue
                all_candidates.extend(self._extract_candidates(q, idx, results))
            except ValueError as exc:
                failures.append(f"{self._query_label(idx)} failed: {exc}. Query: {q}")
            except Exception as exc:  # noqa: BLE001
                failures.append(
                    f"{self._query_label(idx)} failed with {type(exc).__name__}: {exc}. Query: {q}"
                )

        if not all_candidates and failures:
            return "\n".join(failures)
        if not all_candidates:
            return "No results found. Try a more specific or alternative query."

        merged = self._merge_candidates(all_candidates)
        return self._format_results(queries, merged, failures)

    # ── HTTP plumbing ───────────────────────────────────────────────

    def _build_payload(self, query: str) -> dict:
        return {
            "query": query,
            "max_num_results": self._max_results_per_query,
            "provider": self._provider,
        }

    def _build_headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self._api_key:
            h["Authorization"] = f"Bearer {self._api_key}"
        return h

    @staticmethod
    def _retry_delay(attempt: int) -> float:
        return min(max(0.5 * (2 ** (attempt - 1)), 1.0), 5.0)

    def _request_results(self, query: str) -> dict:
        payload = self._build_payload(query)
        headers = self._build_headers()
        last_error: Exception | None = None

        for attempt in range(1, _MAX_SEARCH_RETRIES + 1):
            try:
                resp = requests.post(
                    self._endpoint_url,
                    json=payload,
                    headers=headers,
                    timeout=self._timeout,
                )
                if resp.status_code == 429 or resp.status_code >= 500:
                    raise _RetryableSearchError(f"Server Error: {resp.status_code}")
                if resp.status_code == 422:
                    raise ValueError(f"URL Unprocessable: {query}")

                try:
                    data = resp.json()
                except json.JSONDecodeError as exc:
                    raise _RetryableSearchError(f"Invalid JSON response: {exc}")
                if not data.get("overall_success"):
                    err_msg = data.get("error_message") or "Unknown error"
                    raise _RetryableSearchError(f"Search API error: {err_msg}")
                return data
            except ValueError:
                raise
            except (requests.RequestException, _RetryableSearchError) as exc:
                last_error = exc
                if attempt == _MAX_SEARCH_RETRIES:
                    break
                time.sleep(self._retry_delay(attempt))
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                break

        if last_error is None:
            last_error = RuntimeError("Unknown search failure")
        raise last_error

    # ── Candidate processing (verbatim from BrowseComp; provider-agnostic) ─

    @staticmethod
    def _query_label(idx: int) -> str:
        return f"Q{idx + 1}"

    @staticmethod
    def _clean_text(text: str) -> str:
        text = str(text or "")
        text = text.replace("Your browser can't play this video.", " ")
        text = re.sub(r"\s+", " ", text)
        text = re.sub(r"\s*Show results with:.*$", "", text)
        text = re.sub(r"\s*Missing:.*$", "", text)
        return text.strip(" -\n\t")

    @classmethod
    def _truncate_snippet(cls, text: str, limit: int = _MAX_SNIPPET_CHARS) -> str:
        cleaned = cls._clean_text(text)
        if len(cleaned) <= limit:
            return cleaned
        clipped = cleaned[: limit - 3].rsplit(" ", 1)[0].strip()
        if not clipped:
            clipped = cleaned[: limit - 3].strip()
        return f"{clipped}..."

    @staticmethod
    def _canonicalize_url(url: str) -> str:
        raw = str(url or "").strip()
        if not raw:
            return ""
        parts = urlsplit(raw)
        if not parts.scheme or not parts.netloc:
            return raw
        path = parts.path.rstrip("/") or "/"
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, "", ""))

    @staticmethod
    def _domain(url: str) -> str:
        try:
            return urlsplit(url).netloc.lower()
        except Exception:
            return ""

    def _extract_candidates(self, query: str, query_idx: int, results: dict) -> list[dict]:
        candidates: list[dict] = []
        for rank, page in enumerate(results.get("items", []), 1):
            url = str(page.get("url", "")).strip()
            canonical_url = self._canonicalize_url(url)
            if not canonical_url:
                continue
            title = self._clean_text(page.get("title", "No Title")) or "No Title"
            snippet = self._truncate_snippet(page.get("snippets", ""))
            candidates.append(
                {
                    "query": query,
                    "query_idx": query_idx,
                    "query_label": self._query_label(query_idx),
                    "rank": rank,
                    "title": title,
                    "url": url,
                    "canonical_url": canonical_url,
                    "domain": self._domain(url),
                    "snippet": snippet,
                }
            )
        return candidates

    @staticmethod
    def _merge_candidates(candidates: list[dict]) -> list[dict]:
        merged: dict[str, dict] = {}
        for item in candidates:
            key = item["canonical_url"]
            current = merged.get(key)
            if current is None:
                merged[key] = {
                    "title": item["title"],
                    "url": item["url"],
                    "canonical_url": item["canonical_url"],
                    "domain": item["domain"],
                    "best_rank": item["rank"],
                    "rank_sum": item["rank"],
                    "rank_count": 1,
                    "query_labels": [item["query_label"]],
                    "queries": [item["query"]],
                    "snippet": item["snippet"],
                }
                continue

            is_better_rank = item["rank"] < current["best_rank"]
            current["best_rank"] = min(current["best_rank"], item["rank"])
            current["rank_sum"] += item["rank"]
            current["rank_count"] += 1
            if item["query_label"] not in current["query_labels"]:
                current["query_labels"].append(item["query_label"])
                current["queries"].append(item["query"])
            if len(item["snippet"]) > len(current["snippet"]):
                current["snippet"] = item["snippet"]
            if is_better_rank and item["title"]:
                current["title"] = item["title"]
                current["url"] = item["url"]
                current["domain"] = item["domain"]

        merged_items = list(merged.values())
        for item in merged_items:
            item["support"] = len(item["query_labels"])
            item["avg_rank"] = item["rank_sum"] / max(item["rank_count"], 1)

        merged_items.sort(
            key=lambda item: (
                -item["support"],
                item["best_rank"],
                item["avg_rank"],
                item["domain"],
                item["title"],
            )
        )
        return merged_items

    @staticmethod
    def _select_candidates(query_labels: list[str], merged_candidates: list[dict]) -> list[dict]:
        selected: list[dict] = []
        seen_urls: set[str] = set()

        def add_candidate(item: dict) -> bool:
            key = item["canonical_url"]
            if key in seen_urls:
                return False
            seen_urls.add(key)
            selected.append(item)
            return True

        shared_added = 0
        for item in merged_candidates:
            if item["support"] <= 1:
                continue
            if add_candidate(item):
                shared_added += 1
            if shared_added >= _MAX_SHARED_CANDIDATES or len(selected) >= _MAX_RETURNED_CANDIDATES:
                return selected

        per_query_sorted: dict[str, list[dict]] = {}
        for query_label in query_labels:
            per_query_sorted[query_label] = [
                item for item in merged_candidates if query_label in item["query_labels"]
            ]

        while len(selected) < _MAX_RETURNED_CANDIDATES:
            added_this_round = False
            for query_label in query_labels:
                for item in per_query_sorted[query_label]:
                    if add_candidate(item):
                        added_this_round = True
                        break
                if len(selected) >= _MAX_RETURNED_CANDIDATES:
                    return selected
            if not added_this_round:
                break

        if len(selected) < _MAX_RETURNED_CANDIDATES:
            for item in merged_candidates:
                add_candidate(item)
                if len(selected) >= _MAX_RETURNED_CANDIDATES:
                    break

        return selected

    def _format_results(
        self,
        queries: list[str],
        merged_candidates: list[dict],
        failures: list[str],
    ) -> str:
        query_labels = [self._query_label(idx) for idx in range(len(queries))]
        selected_candidates = self._select_candidates(query_labels, merged_candidates)
        lines = [
            f"Search summary for {len(queries)} quer{'y' if len(queries) == 1 else 'ies'}.",
            f"Unique candidate URLs after cross-query deduplication: {len(merged_candidates)}.",
        ]

        for idx, query in enumerate(queries):
            lines.append(f"{self._query_label(idx)}: {query}")

        lines.append("")
        lines.append(
            f"Selected {len(selected_candidates)} candidate URLs "
            f"(shared evidence first, then per-query coverage):"
        )

        for idx, item in enumerate(selected_candidates, 1):
            qlabels = ", ".join(item["query_labels"])
            lines.append(f"{idx}. {item['title']}")
            lines.append(f"   url: {item['url']}")
            if item["domain"]:
                lines.append(f"   domain: {item['domain']}")
            lines.append(
                f"   matched queries: {qlabels} | support: {item['support']} | best rank: {item['best_rank']}"
            )
            if item["snippet"]:
                lines.append(f"   snippet: {item['snippet']}")

        omitted = max(len(merged_candidates) - len(selected_candidates), 0)
        if omitted:
            lines.append("")
            lines.append(f"Additional deduplicated candidates omitted from output: {omitted}.")

        if failures:
            lines.append("")
            lines.append("Search issues:")
            for failure in failures:
                lines.append(f"- {failure}")

        return "\n".join(lines).strip()


def web_search_endpoint_from_env() -> str | None:
    """Resolve the default search endpoint from environment, if any."""
    return os.environ.get("WEB_SEARCH_ENDPOINT")
