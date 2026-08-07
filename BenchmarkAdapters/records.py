"""Immutable run manifests and normalized benchmark results."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from .contracts import AdapterError
from .protocol import BenchmarkMode, canonical_json, write_json_exclusive


class RunStatus(str, Enum):
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    INVALID_ARTIFACT = "invalid_artifact"
    INFRASTRUCTURE_ERROR = "infrastructure_error"


@dataclass(frozen=True)
class RunManifest:
    run_id: str
    protocol_id: str
    protocol_digest: str
    mode: BenchmarkMode
    agent: str
    agent_commit: str
    adapter_commit: str
    source_dirty: bool
    task_id: str
    seed: int
    model: str
    reasoning_effort: str
    temperature: float
    wall_clock_seconds: int
    asset_digests: Mapping[str, str]
    hardware: Mapping[str, Any]
    policies: Mapping[str, str]
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    formal: bool = True

    def validate(self) -> None:
        if not self.run_id or not self.protocol_id or not self.agent or not self.task_id:
            raise AdapterError("manifest identity fields must not be empty")
        for name, digest in {
            "protocol": self.protocol_digest,
            "agent_commit": self.agent_commit,
            "adapter_commit": self.adapter_commit,
            **dict(self.asset_digests),
        }.items():
            minimum = 7 if name in {"agent_commit", "adapter_commit"} else 64
            if len(digest) < minimum:
                raise AdapterError(f"manifest digest is missing or invalid: {name}")
        if self.formal and self.source_dirty:
            raise AdapterError("formal runs require a clean source commit")
        if self.formal and not self.asset_digests:
            raise AdapterError("formal runs require frozen asset digests")
        if self.wall_clock_seconds < 1 or not self.hardware or not self.policies:
            raise AdapterError("manifest requires positive budget, hardware, and policies")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["mode"] = self.mode.value
        payload["asset_digests"] = dict(sorted(self.asset_digests.items()))
        payload["hardware"] = dict(sorted(self.hardware.items()))
        payload["policies"] = dict(sorted(self.policies.items()))
        return payload

    @property
    def digest(self) -> str:
        self.validate()
        return hashlib.sha256(canonical_json(self.to_dict())).hexdigest()

    def write(self, path: Path) -> None:
        write_json_exclusive(path, {**self.to_dict(), "manifest_digest": self.digest})


@dataclass(frozen=True)
class BenchmarkRunResult:
    run_id: str
    protocol_id: str
    protocol_digest: str
    manifest_digest: str
    mode: BenchmarkMode
    agent: str
    task_id: str
    seed: int
    status: RunStatus
    score_valid: bool
    score: float | None
    metrics: Mapping[str, Any]
    artifact_path: str | None
    artifact_sha256: str | None
    wall_clock_seconds: float
    tokens: Mapping[str, int] = field(default_factory=dict)
    cost: Mapping[str, float] = field(default_factory=dict)
    failure_reason: str | None = None
    finished_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def validate(self) -> None:
        if not self.run_id or not self.protocol_id or not self.agent or not self.task_id:
            raise AdapterError("result identity fields must not be empty")
        if len(self.protocol_digest) != 64 or len(self.manifest_digest) != 64:
            raise AdapterError("result requires protocol and manifest digests")
        if self.score_valid and self.score is None:
            raise AdapterError("a valid score must contain a numeric value")
        if self.score_valid and (not self.artifact_path or not self.artifact_sha256):
            raise AdapterError("a valid score requires a hashed final artifact")
        if self.artifact_sha256 is not None and len(self.artifact_sha256) != 64:
            raise AdapterError("invalid artifact SHA-256")
        if self.status is RunStatus.COMPLETED and not self.score_valid:
            raise AdapterError("completed result must contain a valid score")
        if self.status is not RunStatus.COMPLETED and not self.failure_reason:
            raise AdapterError("non-completed result requires a failure reason")
        if self.wall_clock_seconds < 0:
            raise AdapterError("result wall-clock cannot be negative")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["mode"] = self.mode.value
        payload["status"] = self.status.value
        payload["metrics"] = dict(self.metrics)
        payload["tokens"] = dict(self.tokens)
        payload["cost"] = dict(self.cost)
        return payload

    def write(self, path: Path) -> None:
        self.validate()
        write_json_exclusive(path, self.to_dict())


__all__ = ["BenchmarkRunResult", "RunManifest", "RunStatus"]
