from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from .base import Tool
from .bash import BashTool
from .file_read import FileReadTool
from .file_edit import FileEditTool
from .file_write import FileWriteTool
from .grep import GrepTool
from .glob_tool import GlobTool
from .run_training import RunTrainingTool
from .executor_tool import ExecutorTool

if TYPE_CHECKING:
    from ..config import AgentConfig


def get_all_tools(
    cwd: str,
    *,
    agent: Optional[object] = None,
    workspace_dir: Optional[str] = None,
    config: "AgentConfig | None" = None,
) -> list[Tool]:
    """Return all available tools configured for the given working directory.

    Pass `agent` to enable the Executor tool (requires a parent Agent instance).
    """
    bash_timeout_default = config.bash_timeout_default if config else 600
    bash_timeout_max = config.bash_timeout_max if config else 86_400
    training_timeout_default = config.run_training_timeout_default if config else 86_400
    training_timeout_max = config.run_training_timeout_max if config else 604_800
    training_stage_timeouts = config.run_training_stage_timeouts if config else {}
    training_stall = config.run_training_stall_timeout if config else 1_800

    tools: list[Tool] = [
        BashTool(
            cwd=cwd,
            workspace_dir=workspace_dir,
            timeout_default=bash_timeout_default,
            timeout_max=bash_timeout_max,
        ),
        RunTrainingTool(
            cwd=cwd,
            workspace_dir=workspace_dir,
            timeout_default=training_timeout_default,
            timeout_max=training_timeout_max,
            stage_timeouts=training_stage_timeouts,
            stall_timeout=training_stall,
        ),
        FileReadTool(cwd=cwd, workspace_dir=workspace_dir),
        FileEditTool(cwd=cwd, workspace_dir=workspace_dir),
        FileWriteTool(cwd=cwd, workspace_dir=workspace_dir),
        GrepTool(cwd=cwd, workspace_dir=workspace_dir),
        GlobTool(cwd=cwd, workspace_dir=workspace_dir),
    ]
    if agent is not None:
        tools.append(ExecutorTool(cwd=cwd, parent_agent=agent, workspace_dir=workspace_dir))  # type: ignore[arg-type]
    return tools


__all__ = [
    "Tool",
    "BashTool",
    "RunTrainingTool",
    "FileReadTool",
    "FileEditTool",
    "FileWriteTool",
    "GrepTool",
    "GlobTool",
    "ExecutorTool",
    "get_all_tools",
]
