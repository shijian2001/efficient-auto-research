"""Canonical FML task loading owned by the shared benchmark layer."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from ..contracts import AdapterError
from ..protocol import canonical_json, sha256_file


@dataclass(frozen=True)
class FMLScoreRange:
    best: float
    worst: float | str

    def validate(self) -> None:
        if not math.isfinite(float(self.best)):
            raise AdapterError("FML normalized-improvement best value must be finite")
        if self.worst != "baseline" and not math.isfinite(float(self.worst)):
            raise AdapterError("FML normalized-improvement worst value must be finite or baseline")


@dataclass(frozen=True)
class FMLTaskSpec:
    schema_version: int
    task_id: str
    upstream_task_name: str
    task_description: str
    editable_paths: tuple[str, ...]
    readonly_paths: tuple[str, ...]
    development_evaluation_command: str
    heldout_evaluation_command: str
    metric: str
    metric_direction: str
    included_datasets: tuple[str, ...]
    wall_clock_seconds: int
    max_agent_steps: int
    max_evaluator_calls: int
    internal_round_policy: str
    internal_proposal_policy: str
    allowed_dependencies: tuple[str, ...]
    evaluator_environment: str
    output_contract: str
    task_config_path: str
    task_config_sha256: str
    task_asset_digests: Mapping[str, str]
    evaluator_digest: str
    baseline_validation_metric: float
    baseline_test_metric: float
    score_range: FMLScoreRange

    def validate(self) -> None:
        if self.schema_version != 1 or not self.task_id or not self.upstream_task_name:
            raise AdapterError("invalid canonical FML task identity")
        if not self.task_description.strip():
            raise AdapterError("canonical FML task description is empty")
        if not self.editable_paths or len(set(self.editable_paths)) != len(self.editable_paths):
            raise AdapterError("canonical FML task requires unique editable paths")
        for value in (*self.editable_paths, *self.readonly_paths):
            path = PurePosixPath(value)
            if path.is_absolute() or not path.parts or ".." in path.parts:
                raise AdapterError(f"unsafe canonical FML task path: {value}")
        if self.metric_direction not in {"higher", "lower"}:
            raise AdapterError("FML task metric direction must be higher or lower")
        if not self.metric or not self.development_evaluation_command or not self.heldout_evaluation_command:
            raise AdapterError("canonical FML task evaluator contract is incomplete")
        if self.wall_clock_seconds < 1 or self.max_agent_steps < 1 or self.max_evaluator_calls < 1:
            raise AdapterError("canonical FML task budgets must be positive")
        if len(self.task_config_sha256) != 64 or len(self.evaluator_digest) != 64:
            raise AdapterError("canonical FML task digests are invalid")
        if not self.task_asset_digests or any(len(value) != 64 for value in self.task_asset_digests.values()):
            raise AdapterError("canonical FML task asset digests are incomplete")
        if not all(math.isfinite(value) for value in (self.baseline_validation_metric, self.baseline_test_metric)):
            raise AdapterError("canonical FML baseline metrics must be finite")
        self.score_range.validate()

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["task_asset_digests"] = dict(sorted(self.task_asset_digests.items()))
        return payload

    @property
    def digest(self) -> str:
        self.validate()
        return hashlib.sha256(canonical_json(self.to_dict())).hexdigest()

    def agent_payload(self) -> dict[str, Any]:
        """Return the evaluator-neutral task semantics visible to every Agent."""

        return {
            "schema_version": self.schema_version,
            "task_id": self.task_id,
            "task_description": self.task_description,
            "editable_paths": list(self.editable_paths),
            "readonly_paths": list(self.readonly_paths),
            "evaluation_command": "Use only the host-owned development evaluator capability supplied at launch.",
            "metric": self.metric,
            "metric_direction": self.metric_direction,
            "wall_clock_seconds": self.wall_clock_seconds,
            "round_policy": self.internal_round_policy,
            "proposal_policy": self.internal_proposal_policy,
            "max_evaluator_calls": self.max_evaluator_calls,
            "allowed_dependencies": list(self.allowed_dependencies),
            "output_contract": self.output_contract,
        }

    def render(self, *, development_command: str) -> str:
        payload = self.agent_payload()
        lines = [
            "# FML-Bench Canonical Task",
            "",
            str(payload["task_description"]),
            "",
            f"Task ID: {self.task_id}",
            f"Metric: {self.metric} ({self.metric_direction} is better)",
            f"Wall-clock budget: {self.wall_clock_seconds} seconds",
            f"Maximum development evaluator calls: {self.max_evaluator_calls}",
            "Editable paths:",
            *[f"- {path}" for path in self.editable_paths],
            "",
            "All other workspace paths are read-only by contract. Do not install dependencies, access hidden test assets, or inspect evaluator internals.",
            f"Run the host-owned development evaluator with: {development_command}",
            "Leave the final chosen revision in the workspace. The host performs the final held-out evaluation exactly once after the Agent exits.",
        ]
        return "\n".join(lines).strip() + "\n"


def _load_json(path: Path, description: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AdapterError(f"invalid {description}: {path}") from exc
    if not isinstance(payload, dict):
        raise AdapterError(f"{description} must be a JSON object: {path}")
    return payload


def _extract_metric(payload: Mapping[str, Any], metric: str, datasets: tuple[str, ...]) -> float:
    values: list[float] = []
    for dataset, data in payload.items():
        if datasets and dataset not in datasets:
            continue
        if isinstance(data, Mapping) and isinstance(data.get("means"), Mapping):
            value = data["means"].get(metric)
            if isinstance(value, (int, float)) and math.isfinite(float(value)):
                values.append(float(value))
    if not values:
        value = payload.get(metric)
        if isinstance(value, (int, float)) and math.isfinite(float(value)):
            values.append(float(value))
    if not values:
        raise AdapterError(f"FML evaluator output omitted finite metric {metric}")
    return sum(values) / len(values)


def display_metric(task_name: str, value: float) -> float:
    if task_name != "Unlearning_open_unlearning":
        return value
    if value <= 0:
        raise AdapterError("FML unlearning metric must be positive before -log10 transform")
    return -math.log10(value)


def load_fml_task(protocol: Any, task_config: Path, *, upstream_root: Path | None = None) -> FMLTaskSpec:
    import yaml

    root = (upstream_root or protocol.upstream_root).resolve()
    task_config = task_config.resolve()
    try:
        task_config.relative_to(root)
    except ValueError as exc:
        raise AdapterError("FML task configuration is outside the pinned upstream source") from exc
    try:
        task_yaml = yaml.safe_load(task_config.read_text(encoding="utf-8"))
        upstream_name = str(task_yaml["benchmark"]["name"])
    except (OSError, KeyError, TypeError, yaml.YAMLError) as exc:
        raise AdapterError(f"invalid FML task YAML: {task_config}") from exc
    task_root = root / "ml_tasks" / upstream_name
    config_path = task_root / "config.json"
    prompt_path = task_root / "prompt.json"
    baseline_val_path = task_root / "baseline_results/val_info.json"
    baseline_test_path = task_root / "baseline_results/test_info.json"
    config = _load_json(config_path, "FML task config")
    prompt = _load_json(prompt_path, "FML task prompt")
    metrics = task_yaml.get("metrics", {}) if isinstance(task_yaml, dict) else {}
    included_metrics = tuple(str(value) for value in metrics.get("include_metrics", ()))
    included_datasets = tuple(str(value) for value in metrics.get("include_datasets", ()))
    metric = str(config.get("metric") or (included_metrics[0] if len(included_metrics) == 1 else ""))
    direction = str(config.get("metric_direction", ""))
    if direction not in {"higher", "lower"}:
        direction = str(metrics.get("per_metric_direction", {}).get(metric, ""))
    task_description = "\n".join(
        str(prompt[name]).strip()
        for name in ("system", "task_description")
        if str(prompt.get(name, "")).strip()
    )
    editable_paths = tuple(str(value) for value in config.get("target_files", ()))
    baseline_val_raw = _extract_metric(
        _load_json(baseline_val_path, "FML baseline validation result"), metric, included_datasets
    )
    baseline_test_raw = _extract_metric(
        _load_json(baseline_test_path, "FML baseline test result"), metric, included_datasets
    )
    task_key = task_config.stem
    raw_range = protocol.task_score_ranges.get(task_key)
    if not isinstance(raw_range, Mapping):
        raise AdapterError(f"FML protocol omitted normalized-improvement range: {task_key}")
    score_range = FMLScoreRange(best=float(raw_range["best"]), worst=raw_range["worst"])
    asset_paths = (config_path, prompt_path, baseline_val_path, baseline_test_path)
    asset_digests = {
        str(path.relative_to(root)): sha256_file(path)
        for path in asset_paths
        if path.is_file() and not path.is_symlink()
    }
    evaluator_digest = hashlib.sha256(
        canonical_json(
            {
                "development_command": str(config.get("val_command", "")),
                "heldout_command": str(config.get("test_command", "")),
                "metric": metric,
                "direction": direction,
                "included_datasets": list(included_datasets),
                "implementation": dict(sorted(protocol.evaluator_files.items())),
            }
        )
    ).hexdigest()
    spec = FMLTaskSpec(
        schema_version=1,
        task_id=task_key,
        upstream_task_name=upstream_name,
        task_description=task_description,
        editable_paths=editable_paths,
        readonly_paths=("all-workspace-paths-except-editable-paths",),
        development_evaluation_command=str(config.get("val_command", "")),
        heldout_evaluation_command=str(config.get("test_command", "")),
        metric=metric,
        metric_direction=direction,
        included_datasets=included_datasets,
        wall_clock_seconds=protocol.wall_clock_seconds,
        max_agent_steps=protocol.max_agent_steps,
        max_evaluator_calls=protocol.max_evaluator_calls,
        internal_round_policy=protocol.internal_round_policy,
        internal_proposal_policy=protocol.internal_proposal_policy,
        allowed_dependencies=(f"frozen-conda-environment:{config.get('conda_env', '')}",),
        evaluator_environment=str(config.get("conda_env", "")),
        output_contract="one final replayable archive containing only the frozen editable paths",
        task_config_path=str(task_config),
        task_config_sha256=sha256_file(task_config),
        task_asset_digests=asset_digests,
        evaluator_digest=evaluator_digest,
        baseline_validation_metric=display_metric(upstream_name, baseline_val_raw),
        baseline_test_metric=display_metric(upstream_name, baseline_test_raw),
        score_range=score_range,
    )
    spec.validate()
    return spec


def normalized_improvement(spec: FMLTaskSpec, raw_test_metric: float) -> float:
    agent_value = display_metric(spec.upstream_task_name, raw_test_metric)
    baseline = spec.baseline_test_metric
    worst = baseline if spec.score_range.worst == "baseline" else float(spec.score_range.worst)
    denominator = abs(spec.score_range.best - worst)
    if denominator == 0:
        raise AdapterError(f"FML normalized-improvement range is degenerate: {spec.task_id}")
    signed = (
        (agent_value - baseline) / denominator
        if spec.metric_direction == "higher"
        else (baseline - agent_value) / denominator
    )
    return max(signed, 0.0)


def task_win(spec: FMLTaskSpec, raw_test_metric: float) -> bool:
    agent_value = display_metric(spec.upstream_task_name, raw_test_metric)
    return agent_value > spec.baseline_test_metric if spec.metric_direction == "higher" else agent_value < spec.baseline_test_metric


__all__ = [
    "FMLScoreRange",
    "FMLTaskSpec",
    "display_metric",
    "load_fml_task",
    "normalized_improvement",
    "task_win",
]
