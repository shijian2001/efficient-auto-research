"""Shared redaction and sensitive-name helpers."""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


SENSITIVE_NAME_PARTS = (
    "api_key",
    "apikey",
    "access_key",
    "private_key",
    "auth",
    "bearer",
    "cookie",
    "credential",
    "password",
    "secret",
    "session",
    "signature",
    "token",
)


def is_sensitive_name(name: str) -> bool:
    normalized = name.lower().replace("-", "_")
    if normalized == "key" or normalized.endswith("_key"):
        return True
    return any(part in normalized for part in SENSITIVE_NAME_PARTS)


def contains_sensitive_name(value: str) -> bool:
    return any(
        is_sensitive_name(token)
        for token in re.findall(r"[A-Za-z][A-Za-z0-9_-]*", value)
    )


def redact_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return value
    if not parsed.scheme or not parsed.netloc:
        return value
    hostname = parsed.hostname or ""
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    netloc = hostname
    try:
        port = parsed.port
    except ValueError:
        port = None
    if port is not None:
        netloc += f":{port}"
    query = urlencode(
        [
            (name, "<redacted>" if is_sensitive_name(name) else item)
            for name, item in parse_qsl(parsed.query, keep_blank_values=True)
        ]
    )
    return urlunsplit((parsed.scheme, netloc, parsed.path, query, parsed.fragment))


__all__ = [
    "SENSITIVE_NAME_PARTS",
    "contains_sensitive_name",
    "is_sensitive_name",
    "redact_url",
]
