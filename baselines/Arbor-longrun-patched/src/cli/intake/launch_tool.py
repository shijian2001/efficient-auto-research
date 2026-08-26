"""LaunchExperiment tool — agent's signal that it's ready to start.

The tool does not actually launch anything. It records the plan into a
shared LaunchState object and returns a success message. The REPL polls
that state after each agent.run() turn; when set, it exits the loop and
hands the plan to the orchestrator.

We use shared state instead of exceptions because the existing Agent
class catches every tool exception and converts it into a tool error
result, so raising would just confuse the agent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from ...core.tools.base import PathAuthorizer, Tool


PluginMode = Literal["inherit", "load", "disabled"]


@dataclass
class LaunchPlan:
    cwd: str
    instruction: str
    rationale: str = ""
    suggested_max_cycles: int | None = None
    suggested_max_turns: int | None = None
    notes: list[str] = field(default_factory=list)
    plugin: str | None = None
    plugin_profile: str | None = None
    plugin_mode: PluginMode = "inherit"
    unloaded_skills: list[str] = field(default_factory=list)


@dataclass
class LaunchState:
    """Shared between the LaunchExperiment tool and the REPL."""
    plan: LaunchPlan | None = None
    plugin: str | None = None
    plugin_profile: str | None = None
    plugin_mode: PluginMode = "inherit"
    unloaded_skills: list[str] = field(default_factory=list)
    # Launch authorization is controlled by the REPL, not inferred by the LLM.
    # The tool stages exact arguments first; a later real user turn may approve
    # that same immutable plan without another model call.
    pending_plan: LaunchPlan | None = None
    pending_plan_presented: bool = False
    # Set when the user runs `/resume` and picks a past session. Typed Any to
    # avoid importing resume_picker here; it holds a ``ResumableSession``.
    resume_target: Any = None
    # Set when `/resume` picks a saved *conversation* (not a launched run): the
    # chosen ``ConversationRecord``. The REPL reloads it into the live agent and
    # keeps chatting, persisting onward saves to this same record.
    resume_conversation: Any = None

    @property
    def launched(self) -> bool:
        return self.plan is not None


class LaunchExperimentTool(Tool):
    name = "LaunchExperiment"
    description = (
        "Stage the exact research plan that you want to show the user. Calling "
        "this tool does NOT launch anything. It records immutable candidate "
        "arguments; after the tool result, present that plan and ask the user "
        "for approval. The CLI controller launches the same staged plan only "
        "after a later, explicit user confirmation.\n"
        "\n"
        "Before calling this tool you MUST:\n"
        "  1. Know the absolute path of the target project (the `cwd` argument)\n"
        "  2. Have read enough of that project to write a precise instruction\n"
        "  3. Be ready to present the proposed plan in plain language after "
        "this tool returns\n"
        "\n"
        "Do not call this tool speculatively. If required details are unclear, "
        "ask the user a clarifying question first."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "cwd": {
                "type": "string",
                "description": (
                    "Absolute path to the project directory the coordinator will "
                    "work on. Must exist. The user confirmed this is the target."
                ),
            },
            "instruction": {
                "type": "string",
                "description": (
                    "The refined research GOAL the coordinator will pursue. "
                    "One paragraph (3-6 sentences). MUST contain all five "
                    "components below — if any is missing, you are not ready "
                    "to launch:\n"
                    "  1. METRIC: the exact number being optimized, the "
                    "command that prints it, and the direction (max/min). "
                    "E.g. 'maximize `score` from `python run_eval.py "
                    "--split dev`, higher is better'.\n"
                    "  2. BASELINE: current value if cheaply obtainable; "
                    "otherwise state 'baseline unknown — measure during "
                    "INIT'.\n"
                    "  3. AMBITION: 'beat baseline', 'reach <target>', or "
                    "'push as high as possible within the cycle budget'.\n"
                    "  4. SCOPE preference: 'novelty-leaning' (favor new "
                    "methods worth publishing), 'effect-leaning' (any "
                    "trick that moves the metric — leaderboard grind), or "
                    "'mixed'. This changes how the coordinator ideates.\n"
                    "  5. HARD CONSTRAINTS: off-limits files/data, "
                    "forbidden behaviors. At minimum: test split is for "
                    "final validation only, never for iteration. Add "
                    "project-specific items (locked files, must-keep "
                    "behaviors, etc.).\n"
                    "Do NOT prescribe a specific approach, algorithm, or "
                    "change list. Phrase the goal as 'improve X measured "
                    "by Y, subject to constraints Z', not 'change A to B'. "
                    "Anchoring the coordinator to one approach wastes its "
                    "exploration budget."
                ),
            },
            "rationale": {
                "type": "string",
                "description": "1-2 sentence summary of why this plan, for the user's report.",
            },
            "suggested_max_cycles": {
                "type": "integer",
                "description": "How many arbor cycles to budget. Conservative for small benchmarks (1-3).",
            },
            "suggested_max_turns": {
                "type": "integer",
                "description": "Optional hard cap on coordinator ReAct turns. Use to bound cost on tiny tasks.",
            },
            "notes": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional bullet notes (constraints, edge cases) to attach to the plan.",
            },
            "apply_experience": {
                "type": "boolean",
                "description": "Set true when the user agreed to reuse prior experience; "
                               "matched lessons are composed into the instruction.",
            },
            "experience_sessions": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Session names (from 'Prior experience available') you judged "
                               "relevant to this goal. You select; keyword match is only a fallback.",
            },
        },
        "required": ["cwd", "instruction"],
    }
    # Stateful control operation: serialize duplicate calls even though it does
    # not write project files.
    is_read_only = False
    yield_after_execute = True

    def should_yield_after_execute(self, output: str) -> bool:
        return output.startswith("STAGED —")

    def __init__(
        self,
        *,
        cwd: str,
        workspace_dir: str | None = None,
        state: LaunchState,
        path_authorizer: PathAuthorizer | None = None,
    ) -> None:
        super().__init__(
            cwd=cwd,
            workspace_dir=workspace_dir,
            path_authorizer=path_authorizer,
        )
        self._state = state

    async def execute(self, **kwargs: Any) -> str:
        from pathlib import Path as _P

        target_cwd = (kwargs.get("cwd") or "").strip()
        instruction = (kwargs.get("instruction") or "").strip()
        if not target_cwd:
            return ("ERROR: cwd is required. Pass the absolute path of the "
                    "project the user wants to research.")
        if not instruction:
            return "ERROR: instruction is required and must be non-empty"
        if self._state.launched:
            return ("ERROR: experiment was already launched in this session; "
                    "you should stop now and let the coordinator run")
        if self._state.pending_plan is not None:
            return (
                "ERROR: a plan is already staged. Wait for the user's response "
                "instead of staging another plan in the same turn."
            )

        # Validate the target dir exists. If not, tell the agent so it can
        # re-ask the user instead of launching against nothing.
        resolved = _P(target_cwd).expanduser()
        if not resolved.is_absolute():
            return (f"ERROR: cwd must be an absolute path (got {target_cwd!r}). "
                    f"Ask the user for the full path.")
        authorized, blocked = self.authorize_path(str(resolved))
        if blocked:
            return f"BLOCKED: {blocked}"
        resolved = _P(authorized)
        if not resolved.exists() or not resolved.is_dir():
            return (f"ERROR: cwd does not exist or is not a directory: {resolved}. "
                    f"Ask the user to confirm the path.")

        plan = LaunchPlan(
            cwd=str(resolved.resolve()),
            instruction=_with_experience(str(resolved.resolve()), instruction,
                                         kwargs.get("apply_experience"),
                                         kwargs.get("experience_sessions")),
            rationale=(kwargs.get("rationale") or "").strip(),
            suggested_max_cycles=_safe_int(kwargs.get("suggested_max_cycles")),
            suggested_max_turns=_safe_int(kwargs.get("suggested_max_turns")),
            notes=[str(n) for n in (kwargs.get("notes") or []) if str(n).strip()],
            plugin=self._state.plugin,
            plugin_profile=self._state.plugin_profile,
            plugin_mode=self._state.plugin_mode,
            unloaded_skills=list(self._state.unloaded_skills),
        )
        self._state.pending_plan = plan
        self._state.pending_plan_presented = False
        return (
            "STAGED — no experiment has launched. The CLI will present this "
            "exact plan and wait for a later, explicit user confirmation."
        )


def _safe_int(v: Any) -> int | None:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _with_experience(cwd: str, instruction: str, apply: Any, sessions: Any = None) -> str:
    """Prepend composed prior experience to the instruction when the user opted in.

    Prefers the sessions the intake agent (an LLM) selected as relevant; falls back
    to keyword topic matching only if it named none.
    """
    if not apply:
        return instruction
    try:
        from ...recall import compose_for_topic, compose_from_sessions
        block = ""
        if isinstance(sessions, list) and sessions:
            block = compose_from_sessions(cwd, [str(s) for s in sessions])
        if not block:
            block = compose_for_topic(cwd, instruction)
    except Exception:  # pylint: disable=broad-exception-caught
        block = ""
    return f"{block}\n\n---\n{instruction}" if block else instruction
