from __future__ import annotations

from aisci_domain_paper.prompts.templates import RESOURCE_DOWNLOAD_SYSTEM_PROMPT
from aisci_domain_paper.subagents.base import PaperSubagent
from aisci_domain_paper.tools import build_resource_download_tools


class ResourceDownloadSubagent(PaperSubagent):
    @property
    def name(self) -> str:
        return "resource_download"

    def system_prompt(self) -> str:
        return RESOURCE_DOWNLOAD_SYSTEM_PROMPT

    def get_tools(self):
        return build_resource_download_tools(self.capabilities)
