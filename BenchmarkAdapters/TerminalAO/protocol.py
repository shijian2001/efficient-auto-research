"""Formal Terminal-Bench Harness Engineering AO protocol."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..contracts import AdapterError
from ..protocol import canonical_json, sha256_file
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
    evaluator_timeout_seconds: int = 86400

    def validate(self) -> None:
        split = FrozenSplit.load(self.split_path)
        baseline = BaselineManifest.load(self.baseline_manifest_path)
        if self.protocol_id != split.protocol_id:
            raise AdapterError("Terminal AO protocol and split IDs differ")
        if self.dataset_digest != split.dataset_digest:
            raise AdapterError("Terminal AO protocol and split dataset digests differ")
        if self.dataset_digest != dataset_tree_digest(self.dataset_path):
            raise AdapterError("Terminal AO dataset tree differs from the frozen protocol")
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
        if self.inner_model != "gpt-5.5":
            raise AdapterError("Terminal AO inner model must be gpt-5.5")
        if self.outer_wall_clock_seconds != 172800 or self.dev_concurrency != 8:
            raise AdapterError("Terminal AO formal budget must be 48h with dev concurrency 8")
        if len(self.seeds) < 3 or len(set(self.seeds)) != len(self.seeds):
            raise AdapterError("Terminal AO formal protocol requires at least three unique seeds")
        if tuple(self.editable_paths) != tuple(baseline.editable_paths):
            raise AdapterError("Terminal AO editable allowlist differs from frozen baseline")
        if not self.retry_policy or not self.failure_policy:
            raise AdapterError("Terminal AO retry and failure policies are required")

    def to_dict(self) -> dict[str, Any]:
        return {
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

    @property
    def digest(self) -> str:
        self.validate()
        return hashlib.sha256(canonical_json(self.to_dict())).hexdigest()

    @classmethod
    def load(cls, path: Path) -> "TerminalAOProtocol":
        payload = json.loads(path.read_text(encoding="utf-8"))
        expected = payload.pop("protocol_digest", None)
        protocol = cls(
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
            evaluator_timeout_seconds=int(payload.get("evaluator_timeout_seconds", 86400)),
        )
        protocol.validate()
        if expected != protocol.digest:
            raise AdapterError(f"Terminal AO protocol digest mismatch: {path}")
        return protocol


__all__ = ["DEFAULT_ASSET_DIR", "TerminalAOProtocol"]
