"""Fail-closed Agent environment routing through a run-local relay."""

from __future__ import annotations

import os
from dataclasses import replace
from typing import Mapping, Sequence

from ..contracts import AdapterError, CommandSpec
from ..process import relay_client_env


LLM_CREDENTIAL_ENV_NAMES = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "AZURE_OPENAI_API_KEY",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "CODEX_API_KEY",
    "COHERE_API_KEY",
    "DEEPSEEK_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "GPT_API_KEY",
    "GROQ_API_KEY",
    "MISTRAL_API_KEY",
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
    "TOGETHER_API_KEY",
    "UPSTREAM_API_KEY",
    "XAI_API_KEY",
)

LLM_ENDPOINT_ENV_NAMES = (
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_API_URL",
    "AUTORESEARCH_CODEX_BASE_URL",
    "AZURE_OPENAI_ENDPOINT",
    "DEEPSEEK_API_BASE",
    "GPT_BASE_URL",
    "OPENAI_API_BASE",
    "OPENAI_BASE_URL",
    "OPENROUTER_BASE_URL",
    "UPSTREAM_BASE_URL",
)

_PROXY_ENV_NAMES = (
    "ALL_PROXY",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "all_proxy",
    "http_proxy",
    "https_proxy",
)


def _is_llm_credential_name(name: str) -> bool:
    normalized = name.upper()
    return (
        normalized in LLM_CREDENTIAL_ENV_NAMES
        or normalized.endswith("_API_KEY")
        or normalized.endswith("_AUTH_TOKEN")
        or normalized.endswith("_OAUTH_TOKEN")
    )


def resolve_upstream_api_key(
    names: Sequence[str] = (),
    *,
    environment: Mapping[str, str] | None = None,
) -> str:
    source = os.environ if environment is None else environment
    candidates = tuple(names) + ("UPSTREAM_API_KEY", "OPENAI_API_KEY")
    for name in candidates:
        value = source.get(name)
        if value:
            return value
    raise AdapterError(
        "host-owned LLM relay requires one configured upstream credential"
    )


def relay_agent_environment(
    *,
    base_url: str,
    model: str,
    environment: Mapping[str, str] | None = None,
    inherit_environment: bool = False,
) -> dict[str, str]:
    routed = os.environ.copy() if inherit_environment else {}
    routed.update(dict(environment or {}))
    for name in tuple(routed):
        if (
            _is_llm_credential_name(name)
            or name in LLM_ENDPOINT_ENV_NAMES
            or name in _PROXY_ENV_NAMES
        ):
            routed.pop(name, None)
    routed.update(
        relay_client_env(
            base_url=base_url,
            proxy="",
            model=model,
            include_credentials=False,
        )
    )
    routed.update(
        {
            "ANTHROPIC_API_KEY": "proxy",
            "ANTHROPIC_AUTH_TOKEN": "proxy",
            "OPENAI_API_KEY": "proxy",
            "UPSTREAM_API_KEY": "proxy",
            "NO_PROXY": "localhost,127.0.0.1",
            "no_proxy": "localhost,127.0.0.1",
        }
    )
    return routed


def route_command_through_relay(
    command: CommandSpec,
    *,
    base_url: str,
    model: str,
) -> CommandSpec:
    return replace(
        command,
        env=relay_agent_environment(
            base_url=base_url,
            model=model,
            environment=command.env,
            inherit_environment=command.inherit_env,
        ),
        inherit_env=False,
    )


__all__ = [
    "LLM_CREDENTIAL_ENV_NAMES",
    "LLM_ENDPOINT_ENV_NAMES",
    "relay_agent_environment",
    "resolve_upstream_api_key",
    "route_command_through_relay",
]
