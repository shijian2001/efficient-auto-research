"""Pinned upstream FML-Bench protocol with code-owned Agent adapters."""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from ..contracts import AdapterError
from ..formal_contract import is_placeholder
from ..protocol import canonical_json, sha256_file, write_json_exclusive
from ..registry import AGENTS
from ..registry import ROOT
from ..task_specs import task_spec_digest


FORMAL_PRIMARY_METRICS = {"average_improvement", "win_rate"}
SHARED_ADAPTER_FILES = (
    "BenchmarkAdapters/FMLBench/adapter.py",
    "BenchmarkAdapters/FMLBench/aggregate.py",
    "BenchmarkAdapters/FMLBench/broker.py",
    "BenchmarkAdapters/FMLBench/dev_client.py",
    "BenchmarkAdapters/FMLBench/evaluator.py",
    "BenchmarkAdapters/FMLBench/records.py",
    "BenchmarkAdapters/FMLBench/runner.py",
    "BenchmarkAdapters/FMLBench/sandbox.py",
    "BenchmarkAdapters/FMLBench/task.py",
    "BenchmarkAdapters/FMLBench/workspace.py",
)


@dataclass(frozen=True)
class FMLProtocol:
    schema_version: int
    benchmark_id: str
    protocol_version: str
    upstream_root: Path
    upstream_commit: str
    task_config_paths: tuple[Path, ...]
    task_config_digests: Mapping[str, str]
    evaluator_files: Mapping[str, str]
    shared_adapter_files: Mapping[str, str]
    agent_adapter_digest: str
    internal_round_policy: str
    internal_proposal_policy: str
    wall_clock_seconds: int
    outer_run_ids: tuple[int, ...]
    gpu_type: str
    gpus_per_evaluation: int
    max_concurrent_evaluations: int
    agent_adapter_ids: tuple[str, ...]
    max_agent_steps: int
    max_evaluator_calls: int
    allowed_dependency_policy: str
    task_score_ranges: Mapping[str, Mapping[str, float | str]]
    primary_metric_name: str
    formal_status: str

    @property
    def outer_repetitions(self) -> int:
        return len(self.outer_run_ids)

    @property
    def protocol_frozen(self) -> bool:
        return (
            self.formal_status == "frozen"
            and self.primary_metric_name in FORMAL_PRIMARY_METRICS
        )

    @property
    def protocol_asset_digests(self) -> dict[str, str]:
        from .agents import adapter_registry_digest

        return {
            "task_spec": task_spec_digest("fml-bench"),
            "agent_adapters": adapter_registry_digest(),
            **{
                f"shared_adapter:{name}": digest
                for name, digest in sorted(self.shared_adapter_files.items())
            },
            **{
                f"task:{name}": digest
                for name, digest in sorted(self.task_config_digests.items())
            },
            **{
                f"evaluator:{name}": digest
                for name, digest in sorted(self.evaluator_files.items())
            },
        }

    @property
    def evaluator_digest(self) -> str:
        return hashlib.sha256(
            canonical_json(dict(sorted(self.evaluator_files.items())))
        ).hexdigest()

    def validate(self, *, formal: bool = True) -> None:
        if self.schema_version != 2 or self.benchmark_id != "fml-bench":
            raise AdapterError("unsupported FML formal protocol schema or benchmark ID")
        if is_placeholder(self.protocol_version):
            raise AdapterError("FML protocol_version must be explicitly frozen")
        if len(self.upstream_commit) != 40 or any(
            character not in "0123456789abcdef"
            for character in self.upstream_commit.lower()
        ):
            raise AdapterError("FML protocol requires an immutable upstream commit")
        if not self.upstream_root.is_dir():
            raise AdapterError(f"FML upstream repository is missing: {self.upstream_root}")
        completed = subprocess.run(
            ["git", "-C", str(self.upstream_root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode or completed.stdout.strip() != self.upstream_commit:
            raise AdapterError("FML upstream source commit differs from frozen protocol")
        dirty = subprocess.run(
            ["git", "-C", str(self.upstream_root), "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=False,
        )
        if formal and (dirty.returncode or dirty.stdout.strip()):
            raise AdapterError("formal FML requires a clean pinned upstream repository")
        if len(self.outer_run_ids) not in {1, 3} or len(set(self.outer_run_ids)) != len(
            self.outer_run_ids
        ):
            raise AdapterError("FML requires one or three unique outer run IDs")
        if self.wall_clock_seconds < 1:
            raise AdapterError("FML wall-clock must be positive")
        if self.max_agent_steps < 1 or self.max_evaluator_calls < 1:
            raise AdapterError("FML Agent/evaluator budgets must be positive")
        if formal and self.max_agent_steps <= 1:
            raise AdapterError("formal FML rejects legacy max_steps=1 smoke budgets")
        if is_placeholder(self.internal_round_policy) or is_placeholder(
            self.internal_proposal_policy
        ):
            raise AdapterError(
                "FML upstream round/proposal semantics must be explicitly configured"
            )
        task_names = tuple(path.name for path in self.task_config_paths)
        task_ids = tuple(path.stem for path in self.task_config_paths)
        if (
            not self.task_config_paths
            or len(self.task_config_paths) != len(self.task_config_digests)
            or len(set(path.resolve() for path in self.task_config_paths))
            != len(self.task_config_paths)
            or len(set(task_names)) != len(task_names)
            or len(set(task_ids)) != len(task_ids)
            or set(self.task_config_digests) != set(task_names)
        ):
            raise AdapterError(
                "FML formal protocol requires a frozen task configuration set"
            )
        for path in self.task_config_paths:
            if not path.is_file() or path.is_symlink():
                raise AdapterError(f"FML task config is missing or unsafe: {path}")
            try:
                path.resolve().relative_to(self.upstream_root.resolve())
            except ValueError as exc:
                raise AdapterError(
                    "FML task configs must be inside the pinned upstream repository"
                ) from exc
            if sha256_file(path) != self.task_config_digests.get(path.name):
                raise AdapterError(f"FML task config digest drift: {path.name}")
        if set(self.task_score_ranges) != set(task_ids):
            raise AdapterError("FML protocol requires one normalized score range per task")
        for task_id, score_range in self.task_score_ranges.items():
            if set(score_range) != {"best", "worst"}:
                raise AdapterError(f"invalid FML score range: {task_id}")
            try:
                float(score_range["best"])
                if score_range["worst"] != "baseline":
                    float(score_range["worst"])
            except (TypeError, ValueError) as exc:
                raise AdapterError(f"invalid FML score range value: {task_id}") from exc
        for relative, digest in self.evaluator_files.items():
            relative_path = PurePosixPath(relative)
            if (
                relative_path.is_absolute()
                or not relative_path.parts
                or ".." in relative_path.parts
            ):
                raise AdapterError(f"unsafe FML evaluator path: {relative}")
            path = self.upstream_root / relative
            if (
                not path.is_file()
                or path.is_symlink()
                or sha256_file(path) != digest
            ):
                raise AdapterError(f"FML evaluator implementation drift: {relative}")
        from .agents import FML_AGENT_ADAPTERS, FMLAgentAdapter
        from .agents import adapter_registry_digest

        if adapter_registry_digest() != self.agent_adapter_digest:
            raise AdapterError("FML concrete Agent adapter implementation drift")
        if set(self.shared_adapter_files) != set(SHARED_ADAPTER_FILES):
            raise AdapterError("FML shared adapter implementation allowlist is incomplete")
        for relative, digest in self.shared_adapter_files.items():
            path = ROOT / relative
            if (
                PurePosixPath(relative).is_absolute()
                or ".." in PurePosixPath(relative).parts
                or not path.is_file()
                or path.is_symlink()
                or sha256_file(path) != digest
            ):
                raise AdapterError(f"FML shared adapter implementation drift: {relative}")

        if (
            set(self.agent_adapter_ids) != set(AGENTS)
            or set(FML_AGENT_ADAPTERS) != set(AGENTS)
            or any(
                not issubclass(adapter_class, FMLAgentAdapter)
                or adapter_class.agent_id != agent_id
                for agent_id, adapter_class in FML_AGENT_ADAPTERS.items()
            )
        ):
            raise AdapterError("FML formal protocol requires seven concrete Agent adapters")
        if (
            is_placeholder(self.gpu_type)
            or self.gpus_per_evaluation < 1
            or self.max_concurrent_evaluations < 1
        ):
            raise AdapterError("FML formal hardware profile is incomplete")
        if is_placeholder(self.allowed_dependency_policy):
            raise AdapterError("FML dependency policy must be explicitly configured")
        if formal:
            if self.primary_metric_name not in FORMAL_PRIMARY_METRICS:
                raise AdapterError(
                    "FML primary metric remains unfrozen; choose average_improvement or win_rate"
                )
            if self.formal_status != "frozen":
                raise AdapterError("FML review candidate has not been promoted to frozen")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["upstream_root"] = str(self.upstream_root.resolve())
        payload["task_config_paths"] = [
            str(path.resolve()) for path in self.task_config_paths
        ]
        payload["outer_run_ids"] = list(self.outer_run_ids)
        payload["agent_adapter_ids"] = list(self.agent_adapter_ids)
        payload["task_score_ranges"] = {
            task: dict(sorted(values.items()))
            for task, values in sorted(self.task_score_ranges.items())
        }
        payload["shared_adapter_files"] = dict(sorted(self.shared_adapter_files.items()))
        return payload

    @property
    def digest(self) -> str:
        self.validate(formal=False)
        return hashlib.sha256(canonical_json(self.to_dict())).hexdigest()

    def write(self, path: Path) -> None:
        write_json_exclusive(path, {**self.to_dict(), "protocol_digest": self.digest})

    @classmethod
    def load(cls, path: Path, *, formal: bool = True) -> "FMLProtocol":
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            expected = payload.pop("protocol_digest", None)
            payload["upstream_root"] = (
                Path(payload["upstream_root"]).expanduser().resolve()
            )
            payload["task_config_paths"] = tuple(
                Path(value).expanduser().resolve()
                for value in payload["task_config_paths"]
            )
            payload["outer_run_ids"] = tuple(
                int(value) for value in payload["outer_run_ids"]
            )
            payload["agent_adapter_ids"] = tuple(
                str(value) for value in payload["agent_adapter_ids"]
            )
            payload["task_score_ranges"] = {
                str(task): dict(values)
                for task, values in payload["task_score_ranges"].items()
            }
            protocol = cls(**payload)
        except (
            OSError,
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            raise AdapterError(f"invalid FML protocol: {path}") from exc
        protocol.validate(formal=formal)
        if expected != protocol.digest:
            raise AdapterError(f"FML protocol digest mismatch: {path}")
        return protocol


__all__ = ["FMLProtocol", "FORMAL_PRIMARY_METRICS", "SHARED_ADAPTER_FILES"]
