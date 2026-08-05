from __future__ import annotations

from typing import Any

from aisci_agent_runtime.tools.base import Tool
from aisci_domain_paper.subagents.paper_reader import PaperReaderCoordinator


class ReadPaperTool(Tool):
    def __init__(self, engine) -> None:
        self.engine = engine

    def name(self) -> str:
        return "read_paper"

    def execute(
        self,
        shell,  # noqa: ARG002
        paper_path: str = "/home/paper/paper.md",
        **kwargs: Any,  # noqa: ARG002
    ) -> str:
        self.engine._ensure_workspace()
        if paper_path != "/home/paper/paper.md":
            if not shell.file_exists(paper_path):
                return f"Error reading paper: {paper_path} does not exist."
            shell.write_file("/home/paper/paper.md", shell.read_file(paper_path))

        self.engine.trace.event("subagent_start", "read_paper started.", phase="analyze", payload={})
        result = PaperReaderCoordinator(self.engine).read_paper_structured()
        summary_path = self.engine.analysis_dir / "summary.md"
        self.engine.trace.event(
            "subagent_finish",
            "read_paper completed.",
            phase="analyze",
            payload={"summary": str(summary_path)},
        )
        return self._build_agent_response(result)

    def _build_agent_response(self, result) -> str:
        lines = [
            "# Paper Reading Complete",
            "",
            f"**Total Runtime**: {result.total_runtime_seconds:.1f}s",
            f"**All Success**: {result.all_success}",
            "",
        ]
        if result.failed_subagents:
            lines.append(f"⚠️ **Failed Subagents**: {', '.join(result.failed_subagents)}")
            lines.append("")
        lines.append(result.summary_with_navigation)
        return "\n".join(lines).strip()

    def get_tool_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "read_paper",
                "description": "Read the staged paper bundle and write structured analysis artifacts to /home/agent/paper_analysis/.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "paper_path": {
                            "type": "string",
                            "description": "Path to the paper markdown file.",
                            "default": "/home/paper/paper.md",
                        },
                    },
                    "additionalProperties": False,
                },
            },
        }


def build_read_paper_tool(engine):
    return ReadPaperTool(engine)


__all__ = ["ReadPaperTool", "build_read_paper_tool"]
