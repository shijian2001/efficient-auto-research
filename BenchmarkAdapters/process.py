"""Shared process and relay environment helpers."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .contracts import AdapterError, CommandResult, CommandSpec


DEFAULT_RELAY_BASE_URL = "https://relay.shuai-ederson-clow.xyz/v1"
DEFAULT_PROXY = "http://127.0.0.1:17892"


def relay_client_env(
    *,
    base_url: str = DEFAULT_RELAY_BASE_URL,
    proxy: str = DEFAULT_PROXY,
    model: str = "gpt-5.5",
    include_credentials: bool = True,
) -> dict[str, str]:
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


def run_command(command: CommandSpec, *, log_path: Path | None = None) -> CommandResult:
    try:
        if log_path is None:
            completed = subprocess.run(
                list(command.argv),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                **command.subprocess_kwargs(),
            )
            output = completed.stdout or ""
        else:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("w", encoding="utf-8") as handle:
                completed = subprocess.run(
                    list(command.argv),
                    stdout=handle,
                    stderr=subprocess.STDOUT,
                    **command.subprocess_kwargs(),
                )
            output = log_path.read_text(encoding="utf-8", errors="replace")
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
    "relay_client_env",
    "run_command",
]
