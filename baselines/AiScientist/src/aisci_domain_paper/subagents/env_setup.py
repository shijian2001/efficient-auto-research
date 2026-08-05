from __future__ import annotations

from aisci_domain_paper.prompts.templates import ENV_SETUP_SYSTEM_PROMPT
from aisci_domain_paper.subagents.base import PaperSubagent
from aisci_domain_paper.tools import build_env_setup_tools


class EnvSetupSubagent(PaperSubagent):
    @property
    def name(self) -> str:
        return "env_setup"

    def system_prompt(self) -> str:
        return ENV_SETUP_SYSTEM_PROMPT

    def get_tools(self):
        return build_env_setup_tools()
