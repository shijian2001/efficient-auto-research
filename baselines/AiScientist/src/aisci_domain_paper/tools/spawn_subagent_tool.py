from __future__ import annotations

from aisci_domain_paper.tools.basic_tool import (
    SubmitTool,
    build_main_direct_tools,
    callback_tool,
)
from aisci_domain_paper.tools.clean_validation_tool import build_clean_validation_tool
from aisci_domain_paper.tools.experiment_tool import build_run_experiment_tool
from aisci_domain_paper.tools.implementation_tool import build_implement_tool
from aisci_domain_paper.tools.paper_reader_tool import build_read_paper_tool
from aisci_domain_paper.tools.prioritization_tool import build_prioritize_tasks_tool


def build_spawn_subagent_tool(engine):
    return callback_tool(
        "spawn_subagent",
        "Run a focused helper subagent for read-only exploration, planning, or general auxiliary work.",
        {
            "type": "object",
            "properties": {
                "subagent_type": {
                    "type": "string",
                    "enum": ["explore", "plan", "general"],
                },
                "task": {"type": "string"},
                "context": {"type": "string"},
                "time_budget": {"type": "integer"},
            },
            "required": ["subagent_type", "task"],
            "additionalProperties": False,
        },
        lambda shell, subagent_type, task, context="", time_budget=None, max_steps=None: engine.run_named_subagent(
            subagent_type=subagent_type,
            objective=task,
            context=context,
            max_steps=max_steps,
            time_limit=time_budget,
        ),
    )


def build_main_tools(engine):
    return [
        *build_main_direct_tools(engine._capabilities()),
        build_read_paper_tool(engine),
        build_prioritize_tasks_tool(engine),
        build_implement_tool(engine),
        build_run_experiment_tool(engine),
        build_spawn_subagent_tool(engine),
        build_clean_validation_tool(engine),
        SubmitTool(),
    ]


__all__ = ["build_main_tools", "build_spawn_subagent_tool"]
