"""Terminal-Bench 36/53 Harness Engineering AO command contract."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..contracts import AdapterError, CommandSpec, require_directory, require_file
from ..process import DEFAULT_PROXY, DEFAULT_RELAY_BASE_URL, relay_client_env
from ..registry import AGENTS, ROOT


@dataclass(frozen=True)
class TerminalAORequest:
    agent: str
    protocol_path: Path
    output_dir: Path
    model: str = "gpt-5.5"
    upstream_base_url: str = DEFAULT_RELAY_BASE_URL
    proxy: str = DEFAULT_PROXY
    seed: int = 0
    timeout_seconds: int = 172800
    dry_run: bool = False


class TerminalAOAdapter:
    def __init__(self, agent: str):
        if agent not in AGENTS:
            raise AdapterError(f"unknown baseline agent: {agent}")
        self.agent = agent

    def build_command(self, request: TerminalAORequest) -> CommandSpec:
        if request.agent != self.agent:
            raise AdapterError("request agent does not match adapter agent")
        require_file(request.protocol_path, "Terminal AO protocol")
        if request.output_dir.exists() and not request.dry_run:
            raise AdapterError(f"Terminal AO output already exists: {request.output_dir.resolve()}")
        if request.timeout_seconds < 1:
            raise AdapterError("Terminal AO timeout must be positive")
        return CommandSpec(
            argv=(
                str(ROOT / "BenchmarkAdapters/.venv/bin/python"),
                "-m",
                "BenchmarkAdapters.TerminalAO.supervisor",
                "--agent",
                self.agent,
                "--protocol",
                str(request.protocol_path.resolve()),
                "--output-dir",
                str(request.output_dir.resolve()),
                "--seed",
                str(request.seed),
                "--model",
                request.model,
                "--upstream-base-url",
                request.upstream_base_url,
                "--proxy",
                request.proxy,
                "--timeout",
                str(request.timeout_seconds),
            ),
            cwd=ROOT,
            env=relay_client_env(
                base_url=request.upstream_base_url,
                proxy=request.proxy,
                model=request.model,
            ),
            timeout_seconds=request.timeout_seconds + 3600,
            label=f"{self.agent} Terminal-Bench 36/53 AO supervisor",
        )


__all__ = ["TerminalAOAdapter", "TerminalAORequest"]
