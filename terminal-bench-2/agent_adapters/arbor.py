"""Harbor 0.20 adapter for Arbor's native ReAct Agent loop."""

from __future__ import annotations

import asyncio
import os
import sys
import threading
from pathlib import Path
from typing import Any

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

from agent_adapters.shared.harbor_shell import (
    HarborShellBridge,
    run_sync_with_cancellation,
)


BASELINE_ROOT = Path(__file__).resolve().parents[2] / "baselines" / "Arbor"
if str(BASELINE_ROOT) not in sys.path:
    sys.path.insert(0, str(BASELINE_ROOT))

from arbor.core import Agent, AgentConfig, create_provider
from arbor.core.tools.bash import BashTool
from arbor.core.tools.file_edit import FileEditTool, _find_actual_string
from arbor.core.tools.file_read import FileReadTool
from arbor.core.tools.file_write import FileWriteTool


SYSTEM_PROMPT = """You are Arbor's autonomous terminal engineering agent.
Solve the user task directly in the provided task container. Inspect files,
make the requested changes, and run relevant public checks. Do not inspect
verifier code or hidden tests. Do not create commits. Use only the supplied
Harbor-backed tools and finish with a concise factual summary.
"""


async def _bridge_call(bridge: HarborShellBridge, callback) -> Any:
    return await run_sync_with_cancellation(
        callback,
        cancel_event=bridge.cancel_event,
    )


class HarborBashTool(BashTool):
    def __init__(self, bridge: HarborShellBridge):
        super().__init__(
            cwd="/",
            timeout_default=600,
            timeout_max=840,
            persist_results=False,
        )
        self.bridge = bridge

    async def _run_foreground(self, command: str, timeout: int) -> str:
        result = await _bridge_call(
            self.bridge,
            lambda: self.bridge.run(command, timeout=timeout),
        )
        output = (result.stdout or "") + (result.stderr or "")
        if result.return_code:
            output += f"\n[Exit code: {result.return_code}]"
        return self._truncate(output)

    async def _run_background(self, command: str, timeout: int) -> str:
        import uuid

        task_id = uuid.uuid4().hex[:8]

        async def worker() -> None:
            try:
                self._background_results[task_id] = await self._run_foreground(
                    command,
                    max(timeout, 840),
                )
            except asyncio.CancelledError:
                self.bridge.cancel_event.set()
                raise
            except Exception as exc:
                self._background_results[task_id] = f"[Background task error: {exc}]"

        self._background_tasks[task_id] = asyncio.create_task(worker())
        return (
            f"Command started in background (task_id: {task_id}).\n"
            f"Command: {command}\n"
            "Continue with other work; the result will be delivered automatically."
        )


class HarborReadTool(FileReadTool):
    def __init__(self, bridge: HarborShellBridge):
        super().__init__(cwd="/", persist_results=False)
        self.bridge = bridge

    async def execute(self, **kwargs: Any) -> str:
        file_path = str(kwargs["file_path"])
        offset = max(0, int(kwargs.get("offset", 0)))
        limit = min(2000, max(1, int(kwargs.get("limit", 500))))
        if not await _bridge_call(
            self.bridge,
            lambda: self.bridge.file_exists(file_path),
        ):
            return f"Error: File not found: {file_path}"
        text = await _bridge_call(
            self.bridge,
            lambda: self.bridge.read_file(file_path),
        )
        lines = text.splitlines()
        selected = lines[offset : offset + limit]
        if not lines:
            return f"Warning: File {file_path} exists but is empty."
        return "\n".join(
            f"{line_number}\t{line}"
            for line_number, line in enumerate(selected, start=offset + 1)
        )


class HarborWriteTool(FileWriteTool):
    def __init__(self, bridge: HarborShellBridge):
        super().__init__(cwd="/", persist_results=False)
        self.bridge = bridge

    async def execute(self, **kwargs: Any) -> str:
        file_path = str(kwargs["file_path"])
        content = str(kwargs["content"])
        existed = await _bridge_call(
            self.bridge,
            lambda: self.bridge.file_exists(file_path),
        )
        await _bridge_call(
            self.bridge,
            lambda: self.bridge.write_file(file_path, content),
        )
        action = "Overwrote" if existed else "Created"
        line_count = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
        return f"{action} {file_path} ({line_count} lines)."


class HarborEditTool(FileEditTool):
    def __init__(self, bridge: HarborShellBridge):
        super().__init__(cwd="/", persist_results=False)
        self.bridge = bridge

    async def execute(self, **kwargs: Any) -> str:
        file_path = str(kwargs["file_path"])
        old_string = str(kwargs["old_string"])
        new_string = str(kwargs["new_string"])
        replace_all = bool(kwargs.get("replace_all", False))
        if old_string == new_string:
            return "Error: old_string and new_string are identical. No change needed."
        if not await _bridge_call(
            self.bridge,
            lambda: self.bridge.file_exists(file_path),
        ):
            return f"Error: File not found: {file_path}"
        content = await _bridge_call(
            self.bridge,
            lambda: self.bridge.read_file(file_path),
        )
        count = content.count(old_string)
        if count == 0:
            actual = _find_actual_string(content, old_string)
            if actual is None:
                return f"Error: old_string not found in {file_path}. Use Read first."
            old_string = actual
            count = content.count(old_string)
        if count > 1 and not replace_all:
            return (
                f"Error: old_string appears {count} times in {file_path}. "
                "Provide more context or set replace_all=true."
            )
        updated = content.replace(old_string, new_string, -1 if replace_all else 1)
        await _bridge_call(
            self.bridge,
            lambda: self.bridge.write_file(file_path, updated),
        )
        replacements = count if replace_all else 1
        return f"Successfully edited {file_path} ({replacements} replacements)."


class ArborTerminalAgent(BaseAgent):
    """Run Arbor's native provider-agnostic Agent with Harbor-backed tools."""

    SUPPORTS_ATIF = False
    SUPPORTS_RESUME = False

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._cancel_event = threading.Event()
        self._bridge: HarborShellBridge | None = None

    @staticmethod
    def name() -> str:
        return "arbor"

    def version(self) -> str:
        return "native-react-harbor-0.20.0"

    async def setup(self, environment: BaseEnvironment) -> None:
        self._cancel_event = threading.Event()
        self._bridge = HarborShellBridge(
            environment,
            cancel_event=self._cancel_event,
        )

    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        if self._bridge is None:
            await self.setup(environment)
        if self._bridge is None:
            raise RuntimeError("Arbor Harbor bridge was not initialized")

        model = self.model_name or os.getenv("ARBOR_MODEL", "gpt-5.5")
        if model.startswith("openai/"):
            model = model.split("/", 1)[1]
        logs_dir = Path(self.logs_dir)
        config = AgentConfig(
            provider="openai-chat",
            model=model,
            api_key=os.getenv("OPENAI_API_KEY"),
            base_url=os.getenv("OPENAI_BASE_URL"),
            llm_timeout=float(os.getenv("ARBOR_LLM_TIMEOUT", "120")),
            llm_provider_retries=0,
            cwd=str(logs_dir),
            workspace_dir=str(logs_dir),
            log_dir=str(logs_dir),
            auto_git=False,
            max_turns=int(os.getenv("ARBOR_TERMINAL_MAX_TURNS", "30")),
            max_tool_concurrency=10,
            track_stats=False,
        )
        provider = create_provider(config)
        native_agent = Agent(
            provider=provider,
            tools=[
                HarborBashTool(self._bridge),
                HarborReadTool(self._bridge),
                HarborWriteTool(self._bridge),
                HarborEditTool(self._bridge),
            ],
            system_prompt=SYSTEM_PROMPT,
            config=config,
        )
        try:
            final_output = await native_agent.run(instruction)
        except asyncio.CancelledError:
            self._cancel_event.set()
            raise

        context.n_input_tokens = native_agent.total_input_tokens
        context.n_output_tokens = native_agent.total_output_tokens
        context.metadata = {
            **(context.metadata or {}),
            "implementation": "native-arbor-react",
            "native_loop": "arbor.core.agent.Agent.run",
            "native_tools": list(native_agent.tools),
            "stop_reason": native_agent.stop_reason,
            "turns": native_agent.total_turns,
            "final_output": final_output,
        }


__all__ = [
    "ArborTerminalAgent",
    "HarborBashTool",
    "HarborEditTool",
    "HarborReadTool",
    "HarborWriteTool",
]
