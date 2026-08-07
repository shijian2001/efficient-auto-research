"""Fail-closed Harbor registration for EAR Terminal-Bench support."""

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext


class EARTerminalAdapterError(RuntimeError):
    """Raised until EAR has clean sibling candidates and best replay."""


class EARTerminalAgent(BaseAgent):
    SUPPORTS_ATIF = False
    SUPPORTS_RESUME = False

    @staticmethod
    def name() -> str:
        return "ear"

    def version(self) -> str:
        return "blocked-candidate-isolation"

    async def setup(self, environment: BaseEnvironment) -> None:
        del environment

    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        del instruction, environment, context
        raise EARTerminalAdapterError(
            "EAR Terminal-Bench is disabled until every graph candidate runs in "
            "a clean sibling environment and only the best candidate is replayed"
        )


__all__ = ["EARTerminalAdapterError", "EARTerminalAgent"]
