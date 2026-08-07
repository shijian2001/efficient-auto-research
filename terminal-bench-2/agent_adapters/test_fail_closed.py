from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from harbor.agents.factory import AgentFactory
from harbor.models.agent.context import AgentContext


@pytest.mark.parametrize(
    "import_path",
    [
        "agent_adapters.ear:EARTerminalAgent",
        "agent_adapters.mlevolve:MLEvolveTerminalAgent",
        "agent_adapters.ml_master_2:MLMaster2Agent",
    ],
)
def test_direct_harbor_registration_fails_closed(
    tmp_path: Path,
    import_path: str,
) -> None:
    agent = AgentFactory.create_agent_from_import_path(
        import_path,
        logs_dir=tmp_path / "logs",
        model_name="gpt-5.5",
    )

    async def scenario() -> None:
        with pytest.raises(RuntimeError, match="disabled until"):
            await agent.run("task", object(), AgentContext())

    asyncio.run(scenario())
