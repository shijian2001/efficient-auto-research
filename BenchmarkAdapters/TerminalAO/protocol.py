"""Formal Terminal-Bench Harness Engineering AO protocol."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..contracts import AdapterError
from ..protocol import canonical_json, sha256_file, write_json_exclusive
from ..task_specs import task_spec_digest
from .baseline import BaselineManifest
from .split import FrozenSplit, dataset_tree_digest


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ASSET_DIR = ROOT / "terminal-bench-2/ao_protocol"


def _portable_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return "repo:" + resolved.relative_to(ROOT).as_posix()
    except ValueError:
        return str(resolved)


def _resolve_path(value: str, protocol_file: Path) -> Path:
    if value.startswith("repo:"):
        return (ROOT / value.removeprefix("repo:")).resolve()
    if value.startswith("asset:"):
        return (protocol_file.parent / value.removeprefix("asset:")).resolve()
    return Path(value).expanduser().resolve()


@dataclass(frozen=True)
class TerminalAOProtocol:
    protocol_id: str
    dataset_path: Path
    dataset_digest: str
    split_path: Path
    split_digest: str
    baseline_source: Path
    baseline_manifest_path: Path
    baseline_manifest_digest: str
    harbor_executable: Path
    harbor_version: str
    harbor_lock_path: Path
    harbor_lock_digest: str
    inner_model: str
    outer_wall_clock_seconds: int
    dev_concurrency: int
    seeds: tuple[int, ...]
    retry_policy: str
    failure_policy: str
    editable_paths: tuple[str, ...]
    benchmark_source_commit: str | None = None
    evaluator_timeout_seconds: int = 86400
    schema_version: int = 1

    def validate(self) -> None:
        if self.schema_version not in {1, 2}:
            raise AdapterError("unsupported Terminal AO protocol schema")
        if self.schema_version == 2 and (
            self.benchmark_source_commit is None
            or len(self.benchmark_source_commit) != 40
            or any(
                character not in "0123456789abcdef"
                for character in self.benchmark_source_commit.lower()
            )
        ):
            raise AdapterError("Terminal AO schema v2 requires an immutable benchmark source commit")
        split = FrozenSplit.load(self.split_path)
        baseline = BaselineManifest.load(self.baseline_manifest_path)
        if self.protocol_id != split.protocol_id:
            raise AdapterError("Terminal AO protocol and split IDs differ")
        if self.dataset_digest != split.dataset_digest:
            raise AdapterError("Terminal AO protocol and split dataset digests differ")
        actual_dataset_digest = dataset_tree_digest(self.dataset_path)
        if self.dataset_digest != actual_dataset_digest:
            raise AdapterError(
                "Terminal AO dataset tree differs from the frozen protocol: "
                f"expected={self.dataset_digest} actual={actual_dataset_digest}"
            )
        if self.split_digest != split.digest:
            raise AdapterError("Terminal AO split asset differs from the frozen protocol")
        if not self.dataset_path.is_dir() or len(list(self.dataset_path.glob("*/task.toml"))) != 89:
            raise AdapterError("Terminal AO protocol requires the frozen 89-task dataset")
        if self.baseline_manifest_digest != baseline.digest:
            raise AdapterError("Terminal AO baseline manifest differs from the frozen protocol")
        if self.harbor_version != "0.20.0" or baseline.harbor_version != "0.20.0":
            raise AdapterError("Terminal AO protocol requires Harbor 0.20.0")
        if not self.harbor_executable.is_file() or not self.baseline_source.is_dir():
            raise AdapterError("Terminal AO Harbor executable or terminus baseline is missing")
        if not self.harbor_lock_path.is_file() or sha256_file(self.harbor_lock_path) != self.harbor_lock_digest:
            raise AdapterError("Terminal AO Harbor dependency lock differs from the frozen protocol")
        baseline.verify_source(self.baseline_source)
        if not self.inner_model.strip():
            raise AdapterError("Terminal AO legacy inner model field must not be empty")
        if self.outer_wall_clock_seconds != 172800 or self.dev_concurrency != 8:
            raise AdapterError("Terminal AO formal budget must be 48h with dev concurrency 8")
        if len(self.seeds) not in {1, 3} or len(set(self.seeds)) != len(self.seeds):
            raise AdapterError("Terminal AO requires one or three unique outer run IDs")
        if tuple(self.editable_paths) != tuple(baseline.editable_paths):
            raise AdapterError("Terminal AO editable allowlist differs from frozen baseline")
        if not self.retry_policy or not self.failure_policy:
            raise AdapterError("Terminal AO retry and failure policies are required")

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "protocol_id": self.protocol_id,
            "dataset_path": _portable_path(self.dataset_path),
            "dataset_digest": self.dataset_digest,
            "split_path": _portable_path(self.split_path),
            "split_digest": self.split_digest,
            "baseline_source": _portable_path(self.baseline_source),
            "baseline_manifest_path": _portable_path(self.baseline_manifest_path),
            "baseline_manifest_digest": self.baseline_manifest_digest,
            "harbor_executable": _portable_path(self.harbor_executable),
            "harbor_version": self.harbor_version,
            "harbor_lock_path": _portable_path(self.harbor_lock_path),
            "harbor_lock_digest": self.harbor_lock_digest,
            "inner_model": self.inner_model,
            "outer_wall_clock_seconds": self.outer_wall_clock_seconds,
            "dev_concurrency": self.dev_concurrency,
            "seeds": list(self.seeds),
            "retry_policy": self.retry_policy,
            "failure_policy": self.failure_policy,
            "editable_paths": list(self.editable_paths),
            "evaluator_timeout_seconds": self.evaluator_timeout_seconds,
        }
        if self.schema_version != 1:
            payload["schema_version"] = self.schema_version
            payload["benchmark_source_commit"] = self.benchmark_source_commit
        return payload

    @property
    def outer_repetitions(self) -> int:
        return len(self.seeds)

    def require_formal_contract(self) -> None:
        if (
            self.schema_version != 2
            or self.inner_model != "configured-by-model-track"
            or self.benchmark_source_commit is None
        ):
            raise AdapterError(
                "formal Terminal AO requires a reviewed schema-v2 model-track protocol candidate"
            )

    def protocol_asset_digests(self) -> dict[str, str]:
        baseline = BaselineManifest.load(self.baseline_manifest_path)
        return {
            "task_spec": task_spec_digest("terminal-bench-ao"),
            "dataset": self.dataset_digest,
            "split": self.split_digest,
            "baseline_manifest": self.baseline_manifest_digest,
            "terminus_baseline": baseline.source_tree_digest,
            "harbor_lock": self.harbor_lock_digest,
        }

    @property
    def digest(self) -> str:
        self.validate()
        return hashlib.sha256(canonical_json(self.to_dict())).hexdigest()

    @classmethod
    def _from_payload(cls, payload: dict[str, Any], path: Path) -> "TerminalAOProtocol":
        return cls(
            protocol_id=str(payload["protocol_id"]),
            dataset_path=_resolve_path(str(payload["dataset_path"]), path),
            dataset_digest=str(payload["dataset_digest"]),
            split_path=_resolve_path(str(payload["split_path"]), path),
            split_digest=str(payload["split_digest"]),
            baseline_source=_resolve_path(str(payload["baseline_source"]), path),
            baseline_manifest_path=_resolve_path(str(payload["baseline_manifest_path"]), path),
            baseline_manifest_digest=str(payload["baseline_manifest_digest"]),
            harbor_executable=_resolve_path(str(payload["harbor_executable"]), path),
            harbor_version=str(payload["harbor_version"]),
            harbor_lock_path=_resolve_path(str(payload["harbor_lock_path"]), path),
            harbor_lock_digest=str(payload["harbor_lock_digest"]),
            inner_model=str(payload["inner_model"]),
            outer_wall_clock_seconds=int(payload["outer_wall_clock_seconds"]),
            dev_concurrency=int(payload["dev_concurrency"]),
            seeds=tuple(int(seed) for seed in payload["seeds"]),
            retry_policy=str(payload["retry_policy"]),
            failure_policy=str(payload["failure_policy"]),
            editable_paths=tuple(payload["editable_paths"]),
            benchmark_source_commit=(
                None
                if payload.get("benchmark_source_commit") is None
                else str(payload["benchmark_source_commit"])
            ),
            evaluator_timeout_seconds=int(payload.get("evaluator_timeout_seconds", 86400)),
            schema_version=int(payload.get("schema_version", 1)),
        )

    @classmethod
    def load(cls, path: Path) -> "TerminalAOProtocol":
        payload = json.loads(path.read_text(encoding="utf-8"))
        expected = payload.pop("protocol_digest", None)
        protocol = cls._from_payload(payload, path)
        protocol.validate()
        if expected != protocol.digest:
            raise AdapterError(f"Terminal AO protocol digest mismatch: {path}")
        return protocol

    def write(self, path: Path) -> None:
        write_json_exclusive(path, {**self.to_dict(), "protocol_digest": self.digest})


def build_protocol_candidate(
    *,
    source_path: Path,
    benchmark_source_commit: str,
    outer_repetitions: int,
) -> TerminalAOProtocol:
    if outer_repetitions not in {1, 3}:
        raise AdapterError("Terminal AO outer_repetitions must be 1 or 3")
    payload = json.loads(source_path.read_text(encoding="utf-8"))
    payload.pop("protocol_digest", None)
    source = TerminalAOProtocol._from_payload(payload, source_path)
    current_split = FrozenSplit.load(source.split_path)
    protocol = TerminalAOProtocol(
        **{
            **source.__dict__,
            "schema_version": 2,
            "benchmark_source_commit": benchmark_source_commit,
            "inner_model": "configured-by-model-track",
            "dataset_digest": current_split.dataset_digest,
            "split_digest": current_split.digest,
            "seeds": tuple(range(outer_repetitions)),
        }
    )
    protocol.validate()
    return protocol


__all__ = ["DEFAULT_ASSET_DIR", "TerminalAOProtocol", "build_protocol_candidate"]
