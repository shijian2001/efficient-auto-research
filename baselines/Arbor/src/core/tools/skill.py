"""LoadSkill tool: fetches a registered skill's markdown body on demand."""

from __future__ import annotations

from typing import Any

from .base import Tool
from ..skill_registry import SkillRegistry


class LoadSkillTool(Tool):
    """Load a reference skill document by name. Read-only, safe to parallelize.

    Skills are static markdown reference documents (e.g., checklists, rubrics,
    domain profiles) that the agent should read at specific decision points
    rather than carrying in its system prompt. The skill registry is built
    from <package>/skills/*.md plus <cwd>/.arbor/skills/*.md (the
    latter overrides on name collision).
    """

    name = "LoadSkill"
    is_read_only = True

    def __init__(self, *, cwd: str, registry: SkillRegistry):
        super().__init__(cwd=cwd)
        self._registry = registry
        self.description = self._build_description()
        self.input_schema = self._build_schema()

    def _build_description(self) -> str:
        summaries = self._registry.summaries_with_source()
        if not summaries:
            return (
                "Load a reference skill document on demand. "
                "(No skills are currently registered.)"
            )
        lines = [
            "Load a reference skill document on demand. Skills are checklists, "
            "rubrics, or domain profiles you should consult at specific decision "
            "points (see when_to_apply hints below).",
            "",
            "Available skills:",
        ]
        for name, desc in summaries:
            lines.append(f"- {name}: {desc}")
        return "\n".join(lines)

    def _build_schema(self) -> dict[str, Any]:
        names = self._registry.names()
        skill_prop: dict[str, Any] = {
            "type": "string",
            "description": "Name of the skill to load (see tool description for the list).",
        }
        if names:
            skill_prop["enum"] = names
        return {
            "type": "object",
            "properties": {"skill_name": skill_prop},
            "required": ["skill_name"],
        }

    async def execute(self, **kwargs: Any) -> str:
        skill_name = kwargs.get("skill_name")
        if not skill_name:
            return "Error: 'skill_name' is required."
        skill = self._registry.get(skill_name)
        if skill is None:
            available = ", ".join(self._registry.names()) or "(none)"
            return (
                f"Error: skill '{skill_name}' not found. "
                f"Available: {available}"
            )
        header = f"# Skill: {skill.name}\n"
        if skill.when_to_apply:
            header += f"_When to apply: {skill.when_to_apply}_\n\n"
        else:
            header += "\n"
        return header + skill.body
