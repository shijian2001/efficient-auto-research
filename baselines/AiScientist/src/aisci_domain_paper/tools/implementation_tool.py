from __future__ import annotations

from dataclasses import replace
from typing import Any

from aisci_agent_runtime.subagents.base import SubagentOutput, SubagentStatus
from aisci_agent_runtime.tools.base import Tool
from aisci_domain_paper.configs import DEFAULT_IMPLEMENTATION_CONFIG
from aisci_domain_paper.tools.basic_tool import build_implementation_tools


class ImplementationTool(Tool):
    def __init__(self, engine) -> None:
        self.engine = engine

    def name(self) -> str:
        return "implement"

    def execute(
        self,
        shell,  # noqa: ARG002
        task: str = "",
        mode: str = "full",
        context: str = "",
        time_budget: int | None = None,
        max_steps: int | None = None,
        **kwargs: Any,  # noqa: ARG002
    ) -> str:
        if mode not in {"full", "fix"}:
            return f"Error: invalid mode '{mode}'. Use 'full' or 'fix'."

        session = self.engine.state_manager.create_session("implementation")
        self.engine.state_manager.append_separator(session)
        cfg = replace(
            self.engine.subagent_config("implementation", DEFAULT_IMPLEMENTATION_CONFIG),
            max_steps=max_steps or DEFAULT_IMPLEMENTATION_CONFIG.max_steps,
            time_limit=time_budget or DEFAULT_IMPLEMENTATION_CONFIG.time_limit,
            log_dir=str(session.directory),
        )
        result = self.engine.run_subagent_output(
            self.engine.subagent_class("implementation"),
            objective=self._task_description(task=task, mode=mode, context=context),
            context="",
            config=cfg,
            session=session,
            phase="implement",
            label="implementation",
        )
        self.engine.mark_implementation_run()
        return self._format_result(result, task=task, mode=mode)

    def _task_description(self, *, task: str, mode: str, context: str) -> str:
        if mode == "full":
            parts = [
                "## Implementation Task (Full Scope)",
                "",
                "### Your Task",
                "Read `/home/agent/prioritized_tasks.md` and execute a breadth-first implementation cycle:",
                "1. Create or stabilize the runnable `reproduce.sh` skeleton first.",
                "2. Build scaffolding across P0 tasks before polishing one component too deeply.",
                "3. Fill in the highest-priority logic, validate locally, commit progress, then continue.",
                "",
                "Work autonomously through as many prioritized tasks as possible within this session.",
            ]
        else:
            parts = [
                "## Implementation Task (Fix Mode)",
                "",
                "### Fix the specific issues below",
                task or "See additional context for details.",
            ]

        injected = self.engine.state_manager.recent_exp_history()
        if context.strip():
            parts.extend(["", "### Context from Main Agent", context.strip()])
        if injected:
            parts.extend(
                [
                    "",
                    "### Recent Experiment History (auto-injected, latest session)",
                    "> Cross-check these notes with the current code and git history before deciding on the fix.",
                    "",
                    injected,
                ]
            )
        elif mode == "full":
            parts.extend(
                [
                    "",
                    "### Experiment History",
                    "> No experiment session exists yet. Treat this as the initial implementation round.",
                ]
            )
        return "\n".join(parts).strip()

    def _format_result(self, result: SubagentOutput, *, task: str, mode: str) -> str:
        status_map = {
            SubagentStatus.COMPLETED: "completed",
            SubagentStatus.FAILED: "failed",
            SubagentStatus.TIMEOUT: "timeout",
        }
        mode_label = "Full Scope" if mode == "full" else "Fix"
        task_short = task[:60] + "..." if len(task) > 60 else task
        header = f"[Implementation | {status_map.get(result.status, result.status.value)} | {mode_label}]"
        if task_short:
            header += f" {task_short}"
        header += f" ({result.num_steps} steps, {result.runtime_seconds:.1f}s)"

        lines = [header, "", "## Summary", result.content.strip() or "(no output)", ""]
        if result.log_path:
            lines.extend([f"**Detailed log**: {result.log_path}", ""])

        if result.status == SubagentStatus.COMPLETED:
            lines.extend(
                [
                    "## What's Next?",
                    "- Run `run_experiment()` to validate reproduce.sh end-to-end",
                    "- If experiment fails, use `implement(mode='fix', task='...', context='<diagnosis>')` to fix",
                ]
            )
        elif result.status == SubagentStatus.FAILED:
            lines.extend(
                [
                    "## Error",
                    result.error_message or "The implementation subagent failed.",
                    "",
                    "## Recommendation",
                    "- Review the error and decide how to proceed",
                    "- Re-run with `implement(mode='fix', ...)` if a targeted fix is needed",
                ]
            )
        elif result.status == SubagentStatus.TIMEOUT:
            lines.extend(
                [
                    "## Note",
                    "Implementation timed out. Check if partial progress was made.",
                    "- Review git log for any commits",
                    "- Run `run_experiment()` to validate what was completed",
                ]
            )
        return "\n".join(lines).strip()

    def get_tool_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "implement",
                "description": "Delegate substantial coding work to the implementation subagent.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "task": {"type": "string", "description": "What to build or fix."},
                        "mode": {"type": "string", "enum": ["full", "fix"], "description": "Implementation mode."},
                        "context": {"type": "string", "description": "Feedback or constraints from previous work."},
                        "time_budget": {"type": "integer", "description": "Time budget in seconds."},
                    },
                    "required": [],
                    "additionalProperties": False,
                },
            },
        }


class SpawnEnvSetupTool(Tool):
    def __init__(self, engine) -> None:
        self.engine = engine

    def name(self) -> str:
        return "spawn_env_setup"

    def execute(
        self,
        shell,  # noqa: ARG002
        task: str,
        context: str = "",
        time_budget: int | None = None,
        max_steps: int | None = None,
        **kwargs: Any,  # noqa: ARG002
    ) -> str:
        return self.engine.execute_named_subagent(
            subagent_type="env_setup",
            objective=task,
            context=context,
            max_steps=max_steps,
            time_limit=time_budget,
        )

    def get_tool_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "spawn_env_setup",
                "description": "Delegate environment setup work to the environment setup subagent.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "task": {"type": "string"},
                        "context": {"type": "string"},
                        "time_budget": {"type": "integer"},
                    },
                    "required": ["task"],
                    "additionalProperties": False,
                },
            },
        }


class SpawnResourceDownloadTool(Tool):
    def __init__(self, engine) -> None:
        self.engine = engine

    def name(self) -> str:
        return "spawn_resource_download"

    def execute(
        self,
        shell,  # noqa: ARG002
        task: str,
        context: str = "",
        time_budget: int | None = None,
        max_steps: int | None = None,
        **kwargs: Any,  # noqa: ARG002
    ) -> str:
        return self.engine.execute_named_subagent(
            subagent_type="resource_download",
            objective=task,
            context=context,
            max_steps=max_steps,
            time_limit=time_budget,
        )

    def get_tool_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "spawn_resource_download",
                "description": "Delegate dataset or model download work to the resource download subagent.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "task": {"type": "string"},
                        "context": {"type": "string"},
                        "time_budget": {"type": "integer"},
                    },
                    "required": ["task"],
                    "additionalProperties": False,
                },
            },
        }


def build_implement_tool(engine):
    return ImplementationTool(engine)


def build_spawn_env_setup_tool(engine):
    return SpawnEnvSetupTool(engine)


def build_spawn_resource_download_tool(engine):
    return SpawnResourceDownloadTool(engine)


__all__ = [
    "ImplementationTool",
    "SpawnEnvSetupTool",
    "SpawnResourceDownloadTool",
    "build_implement_tool",
    "build_implementation_tools",
    "build_spawn_env_setup_tool",
    "build_spawn_resource_download_tool",
]
