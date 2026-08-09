"""Shared task contract for autonomous single-file optimization benchmarks."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .contracts import AdapterError


_SAFE_NAME = re.compile(r"^[A-Za-z0-9.][A-Za-z0-9._-]{0,127}$")


@dataclass(frozen=True)
class OptimizationTaskContract:
    task_name: str = "Autoresearch Architecture Design"
    artifact_name: str = "train.py"
    program_name: str = "program.md"
    state_name: str = ".autoresearch-candidate.json"
    metric_name: str = "val_bpb"
    metric_direction: str = "minimize"
    task_instruction: str = (
        "Read program.md and optimize the frozen Autoresearch Architecture Design task. "
        "Edit only train.py."
    )
    native_component: str = ""

    def validate(self) -> None:
        if not self.task_name.strip() or not self.task_instruction.strip():
            raise AdapterError("autonomous optimization task identity must not be empty")
        for value in (self.artifact_name, self.program_name, self.state_name, self.metric_name):
            if (
                value in {".", ".."}
                or not _SAFE_NAME.fullmatch(value)
                or Path(value).name != value
            ):
                raise AdapterError(f"unsafe autonomous optimization task name: {value!r}")
        if self.metric_direction not in {"minimize", "maximize"}:
            raise AdapterError("autonomous optimization metric direction must be minimize or maximize")

    def artifact_path(self, workspace: Path) -> Path:
        return workspace / self.artifact_name

    def program_path(self, workspace: Path) -> Path:
        return workspace / self.program_name

    def state_path(self, workspace: Path) -> Path:
        return workspace / self.state_name

    def score(self, feedback: Mapping[str, object]) -> float | None:
        if not feedback.get("score_valid"):
            return None
        value = feedback.get(self.metric_name)
        if not isinstance(value, (int, float)):
            raise AdapterError(
                f"development feedback is missing numeric metric {self.metric_name!r}"
            )
        return float(value)

    @property
    def metric_sign(self) -> int:
        return -1 if self.metric_direction == "minimize" else 1


def task_contract(environment: Mapping[str, str] | None = None) -> OptimizationTaskContract:
    source = os.environ if environment is None else environment
    contract = OptimizationTaskContract(
        task_name=source.get("OPTIMIZATION_TASK_NAME", OptimizationTaskContract.task_name),
        artifact_name=source.get(
            "OPTIMIZATION_ARTIFACT_NAME", OptimizationTaskContract.artifact_name
        ),
        program_name=source.get("OPTIMIZATION_PROGRAM_NAME", OptimizationTaskContract.program_name),
        state_name=source.get("OPTIMIZATION_STATE_NAME", OptimizationTaskContract.state_name),
        metric_name=source.get("OPTIMIZATION_METRIC_NAME", OptimizationTaskContract.metric_name),
        metric_direction=source.get(
            "OPTIMIZATION_METRIC_DIRECTION", OptimizationTaskContract.metric_direction
        ),
        task_instruction=source.get(
            "OPTIMIZATION_TASK_INSTRUCTION", OptimizationTaskContract.task_instruction
        ),
        native_component=source.get("OPTIMIZATION_NATIVE_COMPONENT", ""),
    )
    contract.validate()
    return contract


__all__ = ["OptimizationTaskContract", "task_contract"]
