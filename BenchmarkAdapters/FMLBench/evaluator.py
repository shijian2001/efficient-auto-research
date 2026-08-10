"""Host-owned development and held-out FML evaluation."""

from __future__ import annotations

import json
import base64
import math
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from ..contracts import AdapterError
from ..formal_contract import verify_hashed_json, write_hashed_json
from ..protocol import sha256_file
from .task import FMLTaskSpec, display_metric, normalized_improvement, task_win
from .workspace import FMLWorkspace, disposable_evaluation_workspace, tree_digest


@dataclass(frozen=True)
class FMLEvaluationRecord:
    schema_version: int
    phase: str
    sequence: int
    task_id: str
    task_spec_digest: str
    candidate_digest: str
    evaluator_digest: str
    status: str
    raw_metric: float | None
    displayed_metric: float | None
    normalized_improvement: float | None
    win: bool | None
    output_path: str | None
    output_sha256: str | None
    wall_clock_seconds: float
    failure_reason: str | None

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)

    def write(self, path: Path) -> str:
        return write_hashed_json(path, self.to_dict(), digest_field="evaluation_record_digest")


def _extract_metric(payload: Mapping[str, Any], task: FMLTaskSpec) -> float:
    values: list[float] = []
    for dataset, data in payload.items():
        if task.included_datasets and dataset not in task.included_datasets:
            continue
        if isinstance(data, Mapping) and isinstance(data.get("means"), Mapping):
            value = data["means"].get(task.metric)
            if isinstance(value, (int, float)) and math.isfinite(float(value)):
                values.append(float(value))
    if not values:
        value = payload.get(task.metric)
        if isinstance(value, (int, float)) and math.isfinite(float(value)):
            values.append(float(value))
    if not values:
        raise AdapterError(f"FML evaluator omitted finite metric {task.metric}")
    return sum(values) / len(values)


class FMLSharedEvaluator:
    def __init__(
        self,
        *,
        task: FMLTaskSpec,
        workspace: FMLWorkspace,
        evidence_dir: Path,
        upstream_root: Path,
        max_calls: int,
        environment: Mapping[str, str] | None = None,
        direct_execution: bool = False,
    ) -> None:
        self.task = task
        self.workspace = workspace
        self.evidence_dir = evidence_dir.resolve()
        self.upstream_root = upstream_root.resolve()
        self.max_calls = max_calls
        self.environment = dict(environment or {})
        self.direct_execution = direct_execution
        self._development_calls = 0
        self._final_consumed = False

    @property
    def development_calls(self) -> int:
        return self._development_calls

    def _run(
        self,
        *,
        phase: str,
        sequence: int,
        command: str,
        workspace: FMLWorkspace | None = None,
    ) -> FMLEvaluationRecord:
        workspace = workspace or self.workspace
        workspace.validate_changes(self.task)
        candidate_digest = tree_digest(workspace.root)
        evaluation_root = self.evidence_dir / f"{phase}-{sequence:04d}"
        candidate = disposable_evaluation_workspace(
            workspace.root, evaluation_root / "candidate"
        )
        task_assets = self.upstream_root / "ml_tasks" / self.task.upstream_task_name
        isolated_assets = evaluation_root / "ml_tasks" / self.task.upstream_task_name
        shutil.copytree(task_assets, isolated_assets, symlinks=False)
        command = command.replace(
            f"../../../ml_tasks/{self.task.upstream_task_name}", str(isolated_assets)
        )
        started = time.monotonic()
        output_path = candidate / "results_tmp" / ("val_info.json" if phase == "development" else "test_info.json")
        environment = {
            "HOME": str(evaluation_root / "home"),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PATH": os.environ.get("PATH", ""),
            "PYTHONNOUSERSITE": "1",
            **self.environment,
        }
        Path(environment["HOME"]).mkdir(parents=True, exist_ok=True)
        argv = (
            ["bash", "-lc", command]
            if self.direct_execution
            else [
                "conda",
                "run",
                "--no-capture-output",
                "-n",
                self.task.evaluator_environment,
                "bash",
                "-lc",
                command,
            ]
        )
        completed = subprocess.run(
            argv,
            cwd=candidate,
            env=environment,
            capture_output=True,
            text=True,
            timeout=max(1, self.task.wall_clock_seconds),
            check=False,
        )
        stdout_path = evaluation_root / "stdout.log"
        stderr_path = evaluation_root / "stderr.log"
        stdout_path.write_text(completed.stdout, encoding="utf-8")
        stderr_path.write_text(completed.stderr, encoding="utf-8")
        raw_metric: float | None = None
        displayed: float | None = None
        improvement: float | None = None
        won: bool | None = None
        failure: str | None = None
        status = "failed"
        output_digest: str | None = None
        if completed.returncode == 0 and output_path.is_file() and not output_path.is_symlink():
            try:
                payload = json.loads(output_path.read_text(encoding="utf-8"))
                raw_metric = _extract_metric(payload, self.task)
                displayed = display_metric(self.task.upstream_task_name, raw_metric)
                if phase == "heldout":
                    improvement = normalized_improvement(self.task, raw_metric)
                    won = task_win(self.task, raw_metric)
                output_digest = sha256_file(output_path)
                status = "completed"
            except (OSError, json.JSONDecodeError, AdapterError) as exc:
                failure = f"{type(exc).__name__}: {exc}"
        else:
            failure = f"evaluator exited {completed.returncode} or omitted {output_path.name}"
        record = FMLEvaluationRecord(
            schema_version=1,
            phase=phase,
            sequence=sequence,
            task_id=self.task.task_id,
            task_spec_digest=self.task.digest,
            candidate_digest=candidate_digest,
            evaluator_digest=self.task.evaluator_digest,
            status=status,
            raw_metric=raw_metric,
            displayed_metric=displayed,
            normalized_improvement=improvement,
            win=won,
            output_path=str(output_path) if output_path.is_file() else None,
            output_sha256=output_digest,
            wall_clock_seconds=time.monotonic() - started,
            failure_reason=failure,
        )
        record.write(evaluation_root / "evaluation-record.json")
        return record

    def evaluate_development(self, *, files: object = None) -> dict[str, Any]:
        if self._development_calls >= self.max_calls:
            raise AdapterError("FML development evaluator call budget is exhausted")
        self._development_calls += 1
        workspace = self.workspace
        if files is not None:
            if not isinstance(files, dict):
                raise AdapterError("FML candidate snapshot must be an object")
            snapshot = self.evidence_dir / "transport-snapshots" / f"request-{self._development_calls:04d}"
            shutil.copytree(self.workspace.root, snapshot, symlinks=False)
            for relative in self.task.editable_paths:
                target = snapshot / relative
                if target.is_dir():
                    shutil.rmtree(target)
                elif target.exists() or target.is_symlink():
                    target.unlink()
            for relative, encoded in files.items():
                if not isinstance(relative, str) or not isinstance(encoded, str):
                    raise AdapterError("FML candidate snapshot entries must be strings")
                target = (snapshot / relative).resolve()
                try:
                    target.relative_to(snapshot.resolve())
                except ValueError as exc:
                    raise AdapterError("FML candidate snapshot path is unsafe") from exc
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    target.write_bytes(base64.b64decode(encoded, validate=True))
                except ValueError as exc:
                    raise AdapterError("FML candidate snapshot is not valid base64") from exc
            workspace = FMLWorkspace(
                root=snapshot,
                initial_manifest=self.workspace.initial_manifest,
                initial_digest=self.workspace.initial_digest,
            )
        record = self._run(
            phase="development",
            sequence=self._development_calls,
            command=self.task.development_evaluation_command,
            workspace=workspace,
        )
        return {
            "status": record.status,
            "task_id": record.task_id,
            "candidate_digest": record.candidate_digest,
            "metric": record.displayed_metric,
            "metric_name": self.task.metric,
            "metric_direction": self.task.metric_direction,
            "evaluation_index": self._development_calls,
            "failure_reason": record.failure_reason,
        }

    def evaluate_final(self) -> FMLEvaluationRecord:
        if self._final_consumed:
            raise AdapterError("FML held-out evaluator can be consumed only once")
        self._final_consumed = True
        return self._run(
            phase="heldout",
            sequence=1,
            command=self.task.heldout_evaluation_command,
        )


def verify_evaluation_record(path: Path, task: FMLTaskSpec) -> dict[str, Any]:
    payload = verify_hashed_json(path, digest_field="evaluation_record_digest")
    output = Path(str(payload.get("output_path", "")))
    if not (
        payload.get("schema_version") == 1
        and payload.get("task_id") == task.task_id
        and payload.get("task_spec_digest") == task.digest
        and payload.get("evaluator_digest") == task.evaluator_digest
        and payload.get("phase") in {"development", "heldout"}
        and payload.get("status") in {"completed", "failed"}
    ):
        raise AdapterError(f"invalid FML evaluation record: {path}")
    if payload.get("status") == "completed" and not (
        output.is_file()
        and not output.is_symlink()
        and sha256_file(output) == payload.get("output_sha256")
    ):
        raise AdapterError(f"FML evaluator output differs from its record: {path}")
    return payload


__all__ = ["FMLEvaluationRecord", "FMLSharedEvaluator", "verify_evaluation_record"]
