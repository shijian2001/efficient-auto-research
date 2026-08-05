from __future__ import annotations

from aisci_domain_paper.subagents.base import PaperSubagent
from aisci_domain_paper.tools import build_experiment_tools


class PaperExperimentSubagent(PaperSubagent):
    @property
    def name(self) -> str:
        return "experiment"

    def system_prompt(self) -> str:
        return self.engine.render_subagent_prompt("experiment")

    def get_tools(self):
        return build_experiment_tools(self.capabilities)
