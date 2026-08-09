"""Shared process and relay environment helpers."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Mapping

from .contracts import AdapterError, CommandResult, CommandSpec
from .security import is_sensitive_name


_SAFE_TELEMETRY_NAMES = {
    "cache_tokens",
    "cached_tokens",
    "completion_tokens",
    "input_tokens",
    "output_tokens",
    "prompt_tokens",
    "reasoning_tokens",
    "token_usage",
    "total_tokens",
}


# Compatibility symbol for older configuration readers.  An empty value is
# intentionally unusable: every launcher must inject its selected relay.
DEFAULT_RELAY_BASE_URL = ""
DEFAULT_PROXY = os.environ.get("BENCHMARK_ADAPTERS_PROXY", "")


def relay_client_env(
    *,
    base_url: str = DEFAULT_RELAY_BASE_URL,
    proxy: str = DEFAULT_PROXY,
    model: str | None = None,
    include_credentials: bool = True,
) -> dict[str, str]:
    if not base_url.strip() or not (model or "").strip():
        raise AdapterError("relay client environment requires an explicit base URL and model")
    normalized_base_url = base_url.rstrip("/")
    anthropic_base_url = (
        normalized_base_url[:-3]
        if normalized_base_url.endswith("/v1")
        else normalized_base_url
    )
    environment = {
        "OPENAI_BASE_URL": normalized_base_url,
        "OPENAI_API_BASE": normalized_base_url,
        "GPT_BASE_URL": normalized_base_url,
        "ANTHROPIC_BASE_URL": anthropic_base_url,
        "GPT_CHAT_MODEL": model,
        "MODEL": model,
    }
    if proxy:
        environment.update(
            {
                "HTTP_PROXY": proxy,
                "HTTPS_PROXY": proxy,
                "http_proxy": proxy,
                "https_proxy": proxy,
            }
        )
    else:
        environment.update(
            {
                "HTTP_PROXY": "",
                "HTTPS_PROXY": "",
                "http_proxy": "",
                "https_proxy": "",
                "ALL_PROXY": "",
                "all_proxy": "",
            }
        )
    credential = os.environ.get("UPSTREAM_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if include_credentials and credential:
        environment.update(
            {
                "OPENAI_API_KEY": credential,
                "UPSTREAM_API_KEY": credential,
                "ANTHROPIC_API_KEY": credential,
            }
        )
    return environment


def redact_process_output(text: str, environment: dict[str, str]) -> str:
    output = re.sub(
        r"(?im)^.*authorization\s*:\s*(?:bearer\s+)?[^\r\n]*$",
        "<redacted-header>",
        text,
    )
    for name, value in environment.items():
        if is_sensitive_name(name) and value:
            output = output.replace(value, "<redacted>")
    return output


def redact_sensitive_payload(payload: object, environment: Mapping[str, str]) -> object:
    if isinstance(payload, Mapping):
        return {
            str(key): redact_sensitive_payload(value, environment)
            for key, value in payload.items()
            if str(key).lower().replace("-", "_") in _SAFE_TELEMETRY_NAMES
            or not is_sensitive_name(str(key))
        }
    if isinstance(payload, list):
        return [redact_sensitive_payload(value, environment) for value in payload]
    if isinstance(payload, tuple):
        return tuple(redact_sensitive_payload(value, environment) for value in payload)
    if isinstance(payload, str):
        return redact_process_output(payload, dict(environment))
    return payload


def run_command(command: CommandSpec, *, log_path: Path | None = None) -> CommandResult:
    try:
        completed = subprocess.run(
            list(command.argv),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            **command.subprocess_kwargs(),
        )
        output = redact_process_output(completed.stdout or "", command.merged_env())
        if log_path is not None:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                with log_path.open("x", encoding="utf-8") as handle:
                    handle.write(output)
            except FileExistsError as exc:
                raise AdapterError(f"refusing to overwrite command log: {log_path}") from exc
    except FileNotFoundError as exc:
        raise AdapterError(f"command executable not found: {command.argv[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise AdapterError(f"{command.label or 'adapter command'} timed out") from exc
    except OSError as exc:
        raise AdapterError(f"could not run {command.label or 'adapter command'}: {exc}") from exc

    return CommandResult(command=command, return_code=completed.returncode, stdout=output)


__all__ = [
    "DEFAULT_PROXY",
    "DEFAULT_RELAY_BASE_URL",
    "redact_process_output",
    "redact_sensitive_payload",
    "relay_client_env",
    "run_command",
]
