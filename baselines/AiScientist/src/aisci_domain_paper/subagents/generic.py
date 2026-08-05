from __future__ import annotations

from aisci_domain_paper.subagents.base import PaperSubagent
from aisci_domain_paper.tools import build_explore_tools, build_general_tools, build_plan_tools


class ExploreSubagent(PaperSubagent):
    @property
    def name(self) -> str:
        return "explore"

    def system_prompt(self) -> str:
        return self.engine.render_subagent_prompt("explore")

    def get_tools(self):
        return build_explore_tools(self.capabilities)


class PlanSubagent(PaperSubagent):
    @property
    def name(self) -> str:
        return "plan"

    def system_prompt(self) -> str:
        return self.engine.render_subagent_prompt("plan")

    def get_tools(self):
        return build_plan_tools(self.capabilities)


class GeneralSubagent(PaperSubagent):
    @property
    def name(self) -> str:
        return "general"

    def system_prompt(self) -> str:
        return self.engine.render_subagent_prompt("general")

    def get_tools(self):
        return build_general_tools(self.capabilities)
