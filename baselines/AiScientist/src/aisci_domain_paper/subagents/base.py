from __future__ import annotations

from typing import Any

from aisci_agent_runtime.subagents.base import Subagent, SubagentConfig


class PaperSubagent(Subagent):
    def __init__(
        self,
        engine,
        shell,
        llm,
        config: SubagentConfig | None = None,
        *,
        objective: str,
        context: str = "",
    ) -> None:
        super().__init__(shell, llm, config)
        self.engine = engine
        self.objective = objective
        self.context = context
        self.capabilities = engine._capabilities()
        self.constraints = engine.constraints()

    def build_context(self) -> str:
        parts = [
            f"Objective:\n{self.objective.strip()}",
            "",
            "Canonical workspace contract:",
            "- /home/paper contains paper inputs",
            "- /home/submission is the implementation repository",
            "- /home/agent contains durable agent artifacts",
        ]
        if self.context.strip():
            parts.extend(["", "Additional context:", self.context.strip()])
        return "\n".join(parts).strip()

    def _post_process_output(self, raw_output: str, artifacts: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        enriched = dict(artifacts)
        enriched.setdefault("objective", self.objective)
        enriched.setdefault("subagent", self.name)
        return raw_output, enriched
