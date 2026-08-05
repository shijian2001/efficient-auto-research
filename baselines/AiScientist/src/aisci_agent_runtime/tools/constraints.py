from __future__ import annotations

import re
from typing import Any, Iterable


def build_blocked_patterns_from_blacklist(blacklist: list[str]) -> dict[str, list[str]]:
    """Build upstream-compatible blocked_search_patterns from blacklist entries."""
    if not blacklist:
        return {}

    url_patterns: list[str] = []
    for raw in blacklist:
        value = str(raw).strip()
        if not value or value.startswith("#") or value.lower() == "none":
            continue
        escaped = re.escape(value).replace(r"\*", ".*")
        url_patterns.append(rf".*{escaped}.*")

    return {"url": url_patterns} if url_patterns else {}


def is_url_blocked(url: str, blocked_patterns: dict[str, list[str]] | None) -> bool:
    if not url or not blocked_patterns:
        return False
    for pattern in blocked_patterns.get("url", []):
        try:
            if re.match(pattern, url, re.IGNORECASE):
                return True
        except re.error:
            continue
    return False


def iter_url_like_values(value: Any) -> Iterable[str]:
    """Yield URL-like string values from nested structures."""
    if isinstance(value, dict):
        for key, item in value.items():
            key_lower = str(key).lower()
            if isinstance(item, str):
                if "url" in key_lower or item.startswith(("http://", "https://")):
                    yield item
            else:
                yield from iter_url_like_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from iter_url_like_values(item)
    elif isinstance(value, str) and value.startswith(("http://", "https://")):
        yield value


def item_hits_blocked_patterns(item: Any, blocked_patterns: dict[str, list[str]] | None) -> bool:
    for candidate in iter_url_like_values(item):
        if is_url_blocked(candidate, blocked_patterns):
            return True
    return False


def filter_blocked_result_items(
    items: list[Any],
    blocked_patterns: dict[str, list[str]] | None,
) -> tuple[list[Any], int]:
    if not blocked_patterns:
        return items, 0
    filtered: list[Any] = []
    blocked_count = 0
    for item in items:
        if item_hits_blocked_patterns(item, blocked_patterns):
            blocked_count += 1
        else:
            filtered.append(item)
    return filtered, blocked_count

