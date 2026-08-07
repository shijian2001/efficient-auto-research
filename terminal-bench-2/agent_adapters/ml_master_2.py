"""Fail-closed Harbor registration for ML-Master 2 Terminal-Bench support."""

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext


class MLMaster2AdapterError(RuntimeError):
    """Raised until ML-Master 2 supports remote Harbor workspaces."""


class MLMaster2Agent(BaseAgent):
    SUPPORTS_ATIF = False
    SUPPORTS_RESUME = False

    @staticmethod
    def name() -> str:
        return "ml-master-2"

    def version(self) -> str:
        return "blocked-remote-workspace"

    async def setup(self, environment: BaseEnvironment) -> None:
        del environment

    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        del instruction, environment, context
        raise MLMaster2AdapterError(
            "ML-Master 2 Terminal-Bench is disabled until all native stages use "
            "Harbor workspace APIs instead of host filesystem paths"
        )


MLMaster2 = MLMaster2Agent

__all__ = ["MLMaster2", "MLMaster2AdapterError", "MLMaster2Agent"]
