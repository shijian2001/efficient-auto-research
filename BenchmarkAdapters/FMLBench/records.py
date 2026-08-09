"""Immutable task-level FML evaluator records."""

from __future__ import annotations

import hashlib
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping

from ..contracts import AdapterError
from ..formal_contract import write_hashed_json
from ..protocol import canonical_json, sha256_file


@dataclass(frozen=True)
class FMLTaskRecord:
    schema_version: int
    protocol_digest: str
    upstream_commit: str
    task_id: str
    outer_run_index: int
    status: str
    score_valid: bool
    raw_score: float | None
    artifact_path: str
    artifact_sha256: str
    upstream_result_path: str
    upstream_result_sha256: str
    evaluator_digest: str
    manifest_digest: str
    internal_rounds_completed: int
    internal_proposals_completed: int
    failure_reason: str | None = None
    token_usage: Mapping[str, int] | None = None
    request_count: int | None = None
    cost: float | None = None

    def validate(self) -> None:
        if self.schema_version != 1 or len(self.protocol_digest) != 64:
            raise AdapterError("invalid FML task record schema or protocol digest")
        if len(self.upstream_commit) != 40 or not self.task_id:
            raise AdapterError("invalid FML task record identity")
        if self.status not in {"completed", "failed", "timed_out", "infrastructure_error"}:
            raise AdapterError("invalid FML task completion status")
        if self.score_valid:
            if self.status != "completed" or self.raw_score is None or not math.isfinite(self.raw_score):
                raise AdapterError("valid FML task score must be finite and completed")
            artifact = Path(self.artifact_path)
            if (
                not artifact.is_file()
                or artifact.is_symlink()
                or sha256_file(artifact) != self.artifact_sha256
            ):
                raise AdapterError("FML task artifact differs from its hash")
            result_path = Path(self.upstream_result_path)
            if (
                not result_path.is_file()
                or result_path.is_symlink()
                or sha256_file(result_path) != self.upstream_result_sha256
            ):
                raise AdapterError("FML upstream evaluator result differs from its hash")
        elif not self.failure_reason:
            raise AdapterError("invalid FML task score requires failure evidence")
        if len(self.evaluator_digest) != 64:
            raise AdapterError("FML task evaluator digest is invalid")
        if len(self.manifest_digest) != 64:
            raise AdapterError("FML task manifest digest is invalid")
        if self.internal_rounds_completed < 0 or self.internal_proposals_completed < 0:
            raise AdapterError("FML internal counters cannot be negative")
        if self.token_usage is not None and any(
            not isinstance(value, int) or value < 0 for value in self.token_usage.values()
        ):
            raise AdapterError("FML token telemetry must contain non-negative integers")
        if self.request_count is not None and self.request_count < 0:
            raise AdapterError("FML request telemetry cannot be negative")
        if self.cost is not None and (not math.isfinite(self.cost) or self.cost < 0):
            raise AdapterError("FML cost telemetry must be finite and non-negative")

    @property
    def digest(self) -> str:
        self.validate()
        return hashlib.sha256(canonical_json(asdict(self))).hexdigest()

    def write(self, path: Path) -> None:
        write_hashed_json(path, asdict(self), digest_field="task_record_digest")


__all__ = ["FMLTaskRecord"]
