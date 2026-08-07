"""Domain-neutral terminal task subagent using AiScientist's native loop."""

from __future__ import annotations

from aisci_agent_runtime.subagents.base import Subagent
from aisci_agent_runtime.tools.base import SubagentCompleteTool, Tool
from aisci_agent_runtime.tools.shell_tools import (
    BashToolWithTimeout,
    FileEditTool,
    PythonTool,
    ReadFileChunkTool,
    SearchFileTool,
)


TERMINAL_TASK_SYSTEM_PROMPT = """You are AiScientist's terminal task subagent.
Complete the user's task directly in the provided workspace.

Use the native shell and file tools to inspect the workspace, make the required
changes, and validate your work. Do not inspect verifier code or hidden tests.
Do not create commits. When the task is complete, call `subagent_complete` with
a concise description of the result and any unresolved risks.
"""


class TerminalTaskSubagent(Subagent):
    """General terminal worker with no domain-specific assumptions."""

    @property
    def name(self) -> str:
        return "terminal_task"

    def system_prompt(self) -> str:
        return TERMINAL_TASK_SYSTEM_PROMPT

    def get_tools(self) -> list[Tool]:
        tool_timeout = min(120, self.config.time_limit)
        return [
            BashToolWithTimeout(
                default_timeout=tool_timeout,
                max_timeout=self.config.time_limit,
            ),
            PythonTool(
                default_timeout=tool_timeout,
                max_timeout=self.config.time_limit,
            ),
            ReadFileChunkTool(),
            SearchFileTool(),
            FileEditTool(),
            SubagentCompleteTool(),
        ]


__all__ = ["TERMINAL_TASK_SYSTEM_PROMPT", "TerminalTaskSubagent"]
