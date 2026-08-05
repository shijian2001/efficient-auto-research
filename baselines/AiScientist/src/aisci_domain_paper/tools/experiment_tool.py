from __future__ import annotations

from dataclasses import replace
from typing import Any

from aisci_agent_runtime.subagents.base import SubagentOutput, SubagentStatus
from aisci_agent_runtime.tools.base import Tool
from aisci_domain_paper.configs import DEFAULT_EXPERIMENT_CONFIG, EXPERIMENT_VALIDATE_TIME_LIMIT
from aisci_domain_paper.tools.basic_tool import build_experiment_tools


class ExperimentTool(Tool):
    def __init__(self, engine) -> None:
        self.engine = engine

    def name(self) -> str:
        return "run_experiment"

    def execute(
        self,
        shell,  # noqa: ARG002
        task: str,
        mode: str = "full",
        context: str = "",
        time_budget: int | None = None,
        max_steps: int | None = None,
        session_kind: str = "experiment",
        **kwargs: Any,  # noqa: ARG002
    ) -> str:
        if mode not in {"full", "validate"}:
            return f"Error: invalid mode '{mode}'. Use 'full' or 'validate'."

        session = self.engine.state_manager.create_session(session_kind)
        self.engine.state_manager.append_separator(session)
        default_budget = DEFAULT_EXPERIMENT_CONFIG.time_limit if mode == "full" else EXPERIMENT_VALIDATE_TIME_LIMIT
        cfg = replace(
            self.engine.subagent_config("experiment", DEFAULT_EXPERIMENT_CONFIG),
            max_steps=max_steps or DEFAULT_EXPERIMENT_CONFIG.max_steps,
            time_limit=time_budget or default_budget,
            log_dir=str(session.directory),
        )
        result = self.engine.run_subagent_output(
            self.engine.subagent_class("experiment"),
            objective=self._task_description(task=task, mode=mode, context=context),
            context="",
            config=cfg,
            session=session,
            phase="validate",
            label="experiment",
        )
        self.engine.mark_experiment_run(session_kind)
        return self._format_result(result, task=task, mode=mode)

    def _task_description(self, *, task: str, mode: str, context: str) -> str:
        parts = [
            "## Experiment Task",
            "",
            "### Your Task",
            task,
            "",
            f"### Mode: {mode.upper()}",
        ]
        if mode == "full":
            parts.append("- Run the full training / evaluation workflow needed for this check.")
        else:
            parts.append("- Run a shorter validation-oriented check, typically reproduce.sh or a smoke test.")
        injected = self.engine.state_manager.recent_impl_history()
        if context.strip():
            parts.extend(["", "### Context from Main Agent", context.strip()])
        if injected:
            parts.extend(
                [
                    "",
                    "### Recent Implementation History (auto-injected, latest session)",
                    "> Cross-check these notes with git history and the current source tree before diagnosing results.",
                    "",
                    injected,
                ]
            )
        else:
            parts.extend(
                [
                    "",
                    "### Implementation History",
                    "> No implementation log is available yet. Inspect git history and the repository directly before running validation.",
                ]
            )
        return "\n".join(parts).strip()

    def _format_result(self, result: SubagentOutput, *, task: str, mode: str) -> str:
        status_map = {
            SubagentStatus.COMPLETED: "completed",
            SubagentStatus.FAILED: "failed",
            SubagentStatus.TIMEOUT: "timeout",
        }
        task_short = task[:50] + "..." if len(task) > 50 else task
        header = f"[Experiment | {status_map.get(result.status, result.status.value)} | {mode}]"
        if task_short:
            header += f" {task_short}"
        header += f" ({result.num_steps} steps, {result.runtime_seconds:.1f}s)"

        lines = [header, "", "## Results", result.content.strip() or "(no output)", ""]
        if result.log_path:
            lines.extend([f"**Detailed log**: {result.log_path}", ""])

        if result.status == SubagentStatus.COMPLETED:
            lines.extend(
                [
                    "## What's Next?",
                    "Read the experiment report above and decide:",
                    "- If successful: move to the next task",
                    "- If failed/partial: use `implement(mode='fix', task='fix ...', context='<diagnosis from above>')` then re-run",
                ]
            )
        elif result.status == SubagentStatus.FAILED:
            lines.extend(
                [
                    "## Error",
                    result.error_message or "The experiment subagent failed.",
                    "",
                    "## What's Next?",
                    "- Review the error and decide how to proceed",
                    "- Use `implement(mode='fix', task='fix ...')` if code changes are needed",
                ]
            )
        elif result.status == SubagentStatus.TIMEOUT:
            lines.extend(
                [
                    "## Note",
                    "Experiment timed out. This could be normal for long-running experiments.",
                    "- Check logs for partial output",
                    "- Consider increasing time_budget or using mode='validate'",
                ]
            )
        return "\n".join(lines).strip()

    def get_tool_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "run_experiment",
                "description": "Delegate experiment execution and validation to the experiment subagent.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "task": {"type": "string", "description": "What to validate or run."},
                        "mode": {"type": "string", "enum": ["full", "validate"], "description": "Experiment mode."},
                        "context": {"type": "string", "description": "Diagnostics or expectations from previous work."},
                        "time_budget": {"type": "integer", "description": "Time budget in seconds."},
                    },
                    "required": ["task"],
                    "additionalProperties": False,
                },
            },
        }


def build_run_experiment_tool(engine):
    return ExperimentTool(engine)


__all__ = ["ExperimentTool", "build_run_experiment_tool", "build_experiment_tools"]
