"""Pinned upstream FML-Bench protocol with fail-closed unknown fields."""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any, Mapping

from ..contracts import AdapterError
from ..formal_contract import is_placeholder
from ..protocol import canonical_json, sha256_file, write_json_exclusive
from ..registry import AGENTS
from ..security import contains_sensitive_name
from ..task_specs import task_spec_digest


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
    internal_round_policy: str
    internal_proposal_policy: str
    wall_clock_seconds: int
    outer_run_ids: tuple[int, ...]
    gpu_type: str
    gpus_per_evaluation: int
    max_concurrent_evaluations: int
    launcher_commands: Mapping[str, tuple[str, ...]]
    allowed_write_paths: tuple[str, ...]
    metric_direction: str
    artifact_relative_path: str
    upstream_result_relative_path: str
    primary_metric_name: str

    @property
    def outer_repetitions(self) -> int:
        return len(self.outer_run_ids)

    @property
    def protocol_asset_digests(self) -> dict[str, str]:
        return {
            "task_spec": task_spec_digest("fml-bench"),
            **{f"task:{name}": digest for name, digest in sorted(self.task_config_digests.items())},
            **{f"evaluator:{name}": digest for name, digest in sorted(self.evaluator_files.items())},
        }

    @property
    def evaluator_digest(self) -> str:
        return hashlib.sha256(canonical_json(dict(sorted(self.evaluator_files.items())))).hexdigest()

    def validate(self, *, formal: bool = True) -> None:
        if self.schema_version != 1 or self.benchmark_id != "fml-bench":
            raise AdapterError("unsupported FML formal protocol schema or benchmark ID")
        if is_placeholder(self.protocol_version):
            raise AdapterError("FML protocol_version must be explicitly frozen")
        if len(self.upstream_commit) != 40 or any(
            character not in "0123456789abcdef" for character in self.upstream_commit.lower()
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
        if is_placeholder(self.internal_round_policy) or is_placeholder(
            self.internal_proposal_policy
        ):
            raise AdapterError("FML upstream round/proposal semantics must be explicitly configured")
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
            raise AdapterError("FML formal protocol requires a frozen task configuration set")
        for path in self.task_config_paths:
            if not path.is_file() or path.is_symlink():
                raise AdapterError(f"FML task config is missing or unsafe: {path}")
            try:
                path.resolve().relative_to(self.upstream_root.resolve())
            except ValueError as exc:
                raise AdapterError("FML task configs must be inside the pinned upstream repository") from exc
            if sha256_file(path) != self.task_config_digests.get(path.name):
                raise AdapterError(f"FML task config digest drift: {path.name}")
        for relative, digest in self.evaluator_files.items():
            relative_path = PurePosixPath(relative)
            if relative_path.is_absolute() or not relative_path.parts or ".." in relative_path.parts:
                raise AdapterError(f"unsafe FML evaluator path: {relative}")
            path = self.upstream_root / relative
            if not path.is_file() or path.is_symlink() or sha256_file(path) != digest:
                raise AdapterError(f"FML evaluator implementation drift: {relative}")
        if set(self.launcher_commands) != set(AGENTS):
            raise AdapterError("FML formal protocol requires launcher contracts for all seven Agents")
        for agent, command in self.launcher_commands.items():
            if not command or any(is_placeholder(value) for value in command):
                raise AdapterError(f"FML formal launcher command is missing: {agent}")
            if any(contains_sensitive_name(value) for value in command):
                raise AdapterError(
                    f"FML launcher must receive credentials through the environment: {agent}"
                )
        if (
            is_placeholder(self.gpu_type)
            or self.gpus_per_evaluation < 1
            or self.max_concurrent_evaluations < 1
        ):
            raise AdapterError("FML formal hardware profile is incomplete")
        if not self.allowed_write_paths:
            raise AdapterError("FML formal protocol requires explicit allowed write paths")
        for relative in self.allowed_write_paths:
            path = PurePosixPath(relative)
            if path.is_absolute() or not path.parts or ".." in path.parts:
                raise AdapterError(f"unsafe FML allowed write path: {relative}")
        if self.metric_direction not in {"minimize", "maximize"}:
            raise AdapterError("FML metric direction must be minimize or maximize")
        if any(
            is_placeholder(value)
            for value in (
                self.artifact_relative_path,
                self.upstream_result_relative_path,
                self.primary_metric_name,
            )
        ):
            raise AdapterError("FML artifact/result/metric mapping must be explicitly configured")
        for relative in (self.artifact_relative_path, self.upstream_result_relative_path):
            path = PurePosixPath(relative)
            if path.is_absolute() or not path.parts or ".." in path.parts:
                raise AdapterError(f"unsafe FML output evidence path: {relative}")
            if relative not in self.allowed_write_paths:
                raise AdapterError(f"FML output evidence is outside allowed_write_paths: {relative}")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["upstream_root"] = str(self.upstream_root.resolve())
        payload["task_config_paths"] = [str(path.resolve()) for path in self.task_config_paths]
        payload["outer_run_ids"] = list(self.outer_run_ids)
        payload["launcher_commands"] = {
            agent: list(command) for agent, command in sorted(self.launcher_commands.items())
        }
        payload["allowed_write_paths"] = list(self.allowed_write_paths)
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
            payload["upstream_root"] = Path(payload["upstream_root"]).expanduser().resolve()
            payload["task_config_paths"] = tuple(
                Path(value).expanduser().resolve() for value in payload["task_config_paths"]
            )
            payload["outer_run_ids"] = tuple(int(value) for value in payload["outer_run_ids"])
            payload["launcher_commands"] = {
                str(agent): tuple(str(value) for value in command)
                for agent, command in payload["launcher_commands"].items()
            }
            payload["allowed_write_paths"] = tuple(payload["allowed_write_paths"])
            protocol = cls(**payload)
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise AdapterError(f"invalid FML protocol: {path}") from exc
        protocol.validate(formal=formal)
        if expected != protocol.digest:
            raise AdapterError(f"FML protocol digest mismatch: {path}")
        return protocol


__all__ = ["FMLProtocol"]
