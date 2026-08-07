"""Fail-closed Harbor registration for MLEvolve Terminal-Bench support."""

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext


class MLEvolveAdapterError(RuntimeError):
    """Raised until MLEvolve has a domain-neutral isolated backend."""


class MLEvolveTerminalAgent(BaseAgent):
    SUPPORTS_ATIF = False
    SUPPORTS_RESUME = False

    @staticmethod
    def name() -> str:
        return "mlevolve"

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
        raise MLEvolveAdapterError(
            "MLEvolve Terminal-Bench is disabled until host file operations are "
            "replaced by an isolated, domain-neutral Harbor candidate backend"
        )


__all__ = ["MLEvolveAdapterError", "MLEvolveTerminalAgent"]
