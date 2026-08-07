"""Harbor Agent wrapper that executes a frozen candidate terminus-2 tree."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext


_MODULES = (
    "asciinema_handler",
    "terminus_json_plain_parser",
    "terminus_xml_plain_parser",
    "tmux_session",
    "terminus_2",
)


def _load_module(name: str, source_dir: Path) -> ModuleType:
    qualified = f"harbor.agents.terminus_2.{name}"
    spec = importlib.util.spec_from_file_location(qualified, source_dir / f"{name}.py")
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load candidate terminus module: {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[qualified] = module
    spec.loader.exec_module(module)
    return module


class CandidateTerminus2(BaseAgent):
    """Delegate Harbor lifecycle calls to a candidate Terminus2 implementation."""

    SUPPORTS_ATIF = True
    SUPPORTS_RESUME = False

    @staticmethod
    def name() -> str:
        return "candidate-terminus-2"

    def __init__(self, *args: Any, source_dir: str, **kwargs: Any) -> None:
        source = Path(source_dir).resolve()
        if not source.is_dir() or not (source / "terminus_2.py").is_file():
            raise ValueError(f"candidate terminus source is invalid: {source}")
        package = ModuleType("harbor.agents.terminus_2")
        package.__path__ = [str(source)]  # type: ignore[attr-defined]
        sys.modules["harbor.agents.terminus_2"] = package
        loaded: dict[str, ModuleType] = {}
        for module_name in _MODULES:
            loaded[module_name] = _load_module(module_name, source)
        candidate_class = getattr(loaded["terminus_2"], "Terminus2")
        self._delegate = candidate_class(*args, **kwargs)
        super().__init__(*args, **kwargs)

    def version(self) -> str:
        return "candidate-source-tree"

    async def setup(self, environment: BaseEnvironment) -> None:
        await self._delegate.setup(environment)

    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        await self._delegate.run(instruction, environment, context)


__all__ = ["CandidateTerminus2"]
