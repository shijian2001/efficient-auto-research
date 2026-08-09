"""Terminal-Bench 36/53 Harness Engineering AO command contract."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..contracts import AdapterError, CommandSpec, require_directory, require_file
from ..formal_contract import ModelTrackConfig
from ..process import DEFAULT_PROXY, relay_client_env
from ..registry import AGENTS, ROOT


@dataclass(frozen=True)
class TerminalAORequest:
    agent: str
    protocol_path: Path
    output_dir: Path
    model: str | None = None
    upstream_base_url: str = ""
    proxy: str = DEFAULT_PROXY
    seed: int = 0
    timeout_seconds: int = 172800
    dry_run: bool = False
    model_config_path: Path | None = None
    agent_variant: str = "default"
    gpu_ids: tuple[str, ...] = ()


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
        if request.model_config_path is None:
            raise AdapterError("Terminal AO requires --model-config")
        model_config = ModelTrackConfig.load(
            request.model_config_path,
            formal=not request.dry_run,
            require_terminal_inner=True,
        )
        if request.model is not None and request.model != model_config.outer_model_id:
            raise AdapterError("Terminal AO model override differs from model track")
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
                model_config.outer_model_id,
                "--model-config",
                str(request.model_config_path.resolve()),
                "--agent-variant",
                request.agent_variant,
                "--upstream-base-url",
                model_config.relay_base_url,
                "--proxy",
                request.proxy,
                "--timeout",
                str(request.timeout_seconds),
                *tuple(
                    value
                    for gpu_id in request.gpu_ids
                    for value in ("--gpu-id", gpu_id)
                ),
            ),
            cwd=ROOT,
            env=relay_client_env(
                base_url=model_config.relay_base_url,
                proxy=request.proxy,
                model=model_config.outer_model_id,
            ),
            timeout_seconds=request.timeout_seconds + 3600,
            label=f"{self.agent} Terminal-Bench 36/53 AO supervisor",
        )


__all__ = ["TerminalAOAdapter", "TerminalAORequest"]
