"""Harbor 0.20.0 adapter for AiScientist's native Subagent loop."""

from __future__ import annotations

import asyncio
import inspect
import os
import sys
import threading
from pathlib import Path
from typing import Any, Callable

try:
    from harbor.agents.base import BaseAgent
    from harbor.environments.base import BaseEnvironment
    from harbor.models.agent.context import AgentContext
except ImportError:  # pragma: no cover - supports isolated seam tests
    class BaseAgent:  # type: ignore[no-redef]
        SUPPORTS_ATIF = False

        def __init__(self, logs_dir=None, model_name=None, logger=None, **kwargs):
            self.logs_dir = logs_dir
            self.model_name = model_name
            self.logger = logger

    BaseEnvironment = Any  # type: ignore[misc,assignment]
    AgentContext = Any  # type: ignore[misc,assignment]


class AiScientistAdapterError(RuntimeError):
    """Raised when the native AiScientist adapter cannot be initialized."""


_BASELINE_ROOT = Path(__file__).resolve().parents[2] / "baselines" / "AiScientist"
_BASELINE_SRC = _BASELINE_ROOT / "src"
if str(_BASELINE_SRC) not in sys.path:
    sys.path.insert(0, str(_BASELINE_SRC))


def _load_shared_bridge() -> tuple[type, Callable[..., Any]]:
    try:
        from agent_adapters.shared.harbor_shell import (
            HarborShellBridge,
            run_sync_with_cancellation,
        )
    except ImportError as exc:
        raise AiScientistAdapterError(
            "AiScientist requires agent_adapters.shared.harbor_shell"
        ) from exc
    return HarborShellBridge, run_sync_with_cancellation


def _construct_bridge(
    factory: type,
    environment: BaseEnvironment,
    cancel_event: threading.Event,
) -> Any:
    return factory(environment=environment, cancel_event=cancel_event)


class AiScientistTerminalAgent(BaseAgent):
    """Expose AiScientist's native per-task Subagent through Harbor."""

    SUPPORTS_ATIF = False
    SUPPORTS_RESUME = False

    def __init__(
        self,
        *args: Any,
        bridge_factory: type | None = None,
        llm_factory: Callable[[], Any] | None = None,
        cancellation_runner: Callable[..., Any] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._bridge_factory = bridge_factory
        self._llm_factory = llm_factory
        self._cancellation_runner = cancellation_runner
        self._bridge: Any | None = None
        self._cancel_event = threading.Event()

    @staticmethod
    def name() -> str:
        return "ai-scientist"

    def version(self) -> str:
        return "native-subagent-harbor-0.20.0"

    async def setup(self, environment: BaseEnvironment) -> None:
        self._cancel_event = threading.Event()
        factory = self._bridge_factory
        if factory is None:
            factory, _ = _load_shared_bridge()
        self._bridge = _construct_bridge(factory, environment, self._cancel_event)
        working_dir = os.getenv("AISCI_TERMINAL_WORKDIR")
        if working_dir:
            self._bridge.cwd = working_dir
            self._bridge._resolved_cwd = working_dir

    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        if self._bridge is None:
            await self.setup(environment)
        if self._bridge is None:
            raise AiScientistAdapterError("Harbor shell bridge is not initialized")

        from aisci_agent_runtime.llm_client import LLMConfig, create_llm_client
        from aisci_agent_runtime.shell_interface import HarborShellInterface
        from aisci_agent_runtime.subagents.base import SubagentConfig
        from aisci_agent_runtime.subagents.terminal_task import TerminalTaskSubagent

        if self._llm_factory is not None:
            llm = self._llm_factory()
        else:
            model_name = self.model_name or os.getenv("AISCI_MODEL")
            if not model_name:
                raise AiScientistAdapterError("A Harbor model_name or AISCI_MODEL is required")
            provider, separator, model = model_name.partition("/")
            if not separator:
                provider = os.getenv("AISCI_PROVIDER", "openai")
                model = model_name
            llm = create_llm_client(
                LLMConfig(
                    provider=provider,
                    model=model,
                    api_mode=os.getenv("AISCI_API_MODE", "completions"),
                    api_key=os.getenv("OPENAI_API_KEY")
                    or os.getenv("AZURE_OPENAI_API_KEY"),
                    base_url=os.getenv("OPENAI_BASE_URL"),
                    azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
                    api_version=os.getenv("OPENAI_API_VERSION"),
                    max_tokens=int(os.getenv("AISCI_MAX_TOKENS", "32768")),
                    request_timeout=float(os.getenv("AISCI_REQUEST_TIMEOUT", "120")),
                    retry_budget=float(os.getenv("AISCI_RETRY_BUDGET", "240")),
                    cancel_check=self._cancel_event.is_set,
                )
            )

        logs_dir = Path(self.logs_dir or "/tmp/ai-scientist-terminal-logs")
        working_dir = os.getenv("AISCI_TERMINAL_WORKDIR") or None
        shell = HarborShellInterface(
            self._bridge,
            working_dir=working_dir,
        )
        subagent = TerminalTaskSubagent(
            shell=shell,
            llm=llm,
            config=SubagentConfig(
                max_steps=int(os.getenv("AISCI_TERMINAL_MAX_STEPS", "50")),
                time_limit=int(os.getenv("AISCI_TERMINAL_TIME_LIMIT", "720")),
                reminder_freq=int(os.getenv("AISCI_TERMINAL_REMINDER_FREQ", "10")),
                log_dir=str(logs_dir / "native_subagent"),
                output_dir=working_dir or "/tmp",
            ),
            cancel_check=self._cancel_event.is_set,
        )
        runner = self._cancellation_runner
        if runner is None:
            _, runner = _load_shared_bridge()

        try:
            output = await _call_cancellable(
                runner,
                subagent.run,
                instruction,
                cancel_event=self._cancel_event,
            )
        except asyncio.CancelledError:
            self._cancel_event.set()
            raise

        tools = subagent.get_tools()
        context.n_input_tokens = output.token_usage.get("input", 0)
        context.n_output_tokens = output.token_usage.get("output", 0)
        context.metadata = {
            **(context.metadata or {}),
            "implementation": "native-ai-scientist-subagent",
            "native_loop": "aisci_agent_runtime.subagents.base.Subagent.run",
            "native_tools": [tool.name() for tool in tools],
            "native_tool_schemas": [tool.get_tool_schema() for tool in tools],
            "supports_atif": False,
            "status": output.status.value,
            "steps": output.num_steps,
            "final_output": output.content,
            "log_path": output.log_path,
        }


async def _call_cancellable(
    runner: Callable[..., Any],
    callback: Callable[..., Any],
    *args: Any,
    cancel_event: threading.Event | None = None,
) -> Any:
    result = runner(lambda: callback(*args), cancel_event=cancel_event)
    if inspect.isawaitable(result):
        return await result
    return result


__all__ = ["AiScientistAdapterError", "AiScientistTerminalAgent"]
