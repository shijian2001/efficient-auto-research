"""Lightweight user-config loader for the CLI.

Reads ~/.arbor/config.yaml (path comes from _app.GLOBAL_CONFIG_FILE) and
returns a dict of CLI defaults. Falls back to ~/.autoresearch/config.yaml
during the rename transition so existing users don't lose settings.

Schema (all fields optional):
    llm:
            provider: openai            # anthropic | openai | litellm
    model: gpt-5.5
      api_key: sk-...
    base_url: http://localhost:4141/v1
            # openai_api: chat          # optional, only for chat-only endpoints

    defaults:
      max_cycles: 40              # CLI default for --max-cycles
      max_turns: 40               # CLI default for --max-turns
"""

from __future__ import annotations

from typing import Any

import yaml

from .._app import GLOBAL_CONFIG_FILE, LEGACY_GLOBAL_CONFIG_FILE


def load_user_defaults() -> dict[str, Any]:
    """Return user defaults merged from ~/.arbor/config.yaml.

    Falls back to ~/.autoresearch/config.yaml if the new path is missing.
    Always returns a dict; missing file is fine.
    """
    path = GLOBAL_CONFIG_FILE if GLOBAL_CONFIG_FILE.exists() else LEGACY_GLOBAL_CONFIG_FILE
    if not path.exists():
        return {}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    return raw or {}


def llm_defaults() -> dict[str, Any]:
    return load_user_defaults().get("llm") or {}


def cli_defaults() -> dict[str, Any]:
    return load_user_defaults().get("defaults") or {}
