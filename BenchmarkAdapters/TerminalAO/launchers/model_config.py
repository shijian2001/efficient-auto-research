"""Explicit model-track settings consumed by native Terminal AO launchers."""

from __future__ import annotations

import json
import os
from typing import Any


def outer_model_parameters() -> dict[str, Any]:
    raw = os.environ.get("TERMINAL_OUTER_MODEL_PARAMETERS", "")
    if not raw:
        raise RuntimeError("TERMINAL_OUTER_MODEL_PARAMETERS must be configured explicitly")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("TERMINAL_OUTER_MODEL_PARAMETERS is invalid JSON") from exc
    if not isinstance(payload, dict) or not payload:
        raise RuntimeError("TERMINAL_OUTER_MODEL_PARAMETERS must be a non-empty object")
    return payload


def request_timeout_seconds() -> int | None:
    raw = os.environ.get("TERMINAL_OUTER_REQUEST_TIMEOUT_SECONDS", "").strip()
    if not raw:
        return None
    value = int(raw)
    if value < 1:
        raise RuntimeError("Terminal outer request timeout must be positive")
    return value


def retry_policy() -> dict[str, Any]:
    raw = os.environ.get("TERMINAL_OUTER_RETRY_POLICY", "")
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("TERMINAL_OUTER_RETRY_POLICY is invalid JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("TERMINAL_OUTER_RETRY_POLICY must be an object")
    return payload


def max_output_tokens(parameters: dict[str, Any]) -> int | None:
    raw = parameters.get("max_output_tokens", parameters.get("max_tokens"))
    if raw is None:
        return None
    value = int(raw)
    if value < 1:
        raise RuntimeError("Terminal outer max output tokens must be positive")
    return value


def max_retries(policy: dict[str, Any]) -> int | None:
    raw = policy.get("max_retries")
    if raw is None and policy.get("max_attempts") is not None:
        raw = int(policy["max_attempts"]) - 1
    if raw is None:
        return None
    value = int(raw)
    if value < 0:
        raise RuntimeError("Terminal outer max retries must be non-negative")
    return value


__all__ = [
    "max_output_tokens",
    "max_retries",
    "outer_model_parameters",
    "request_timeout_seconds",
    "retry_policy",
]
