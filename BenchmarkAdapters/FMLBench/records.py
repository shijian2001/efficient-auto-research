"""Immutable formal FML task records derived from shared evidence."""

from __future__ import annotations

import hashlib
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

from ..contracts import AdapterError
from ..formal_contract import write_hashed_json
from ..protocol import canonical_json, sha256_file


@dataclass(frozen=True)
class FMLTaskRecord:
    schema_version: int
    protocol_digest: str
    upstream_commit: str
    agent_id: str
    agent_identity_digest: str
    task_id: str
    task_config_digest: str
    canonical_task_digest: str
    rendered_prompt_digest: str
    initial_workspace_digest: str
    outer_run_index: int
    status: str
    score_valid: bool
    raw_test_metric: float | None
    displayed_test_metric: float | None
    normalized_improvement: float | None
    win: bool | None
    artifact_path: str
    artifact_sha256: str
    agent_result_path: str
    agent_result_sha256: str
    evaluation_record_path: str
    evaluation_record_sha256: str
    evaluator_digest: str
    manifest_digest: str
    internal_rounds_completed: int
    internal_proposals_completed: int
    development_evaluator_calls: int
    stdout_path: str
    stderr_path: str
    failure_reason: str | None = None
    token_usage: Mapping[str, int] | None = None
    request_count: int | None = None
    cost: float | None = None

    @property
    def raw_score(self) -> float | None:
        return self.normalized_improvement

    def validate(self) -> None:
        if self.schema_version != 2 or len(self.protocol_digest) != 64:
            raise AdapterError("invalid FML task record schema or protocol digest")
        if len(self.upstream_commit) != 40 or not self.task_id or not self.agent_id:
            raise AdapterError("invalid FML task record identity")
        for digest in (
            self.agent_identity_digest,
            self.task_config_digest,
            self.canonical_task_digest,
            self.rendered_prompt_digest,
            self.initial_workspace_digest,
            self.evaluator_digest,
            self.manifest_digest,
        ):
            if len(digest) != 64:
                raise AdapterError("invalid FML task evidence digest")
        if self.status not in {"completed", "failed", "timed_out", "infrastructure_error"}:
            raise AdapterError("invalid FML task completion status")
        if self.score_valid:
            values = (
                self.raw_test_metric,
                self.displayed_test_metric,
                self.normalized_improvement,
            )
            if (
                self.status != "completed"
                or any(value is None or not math.isfinite(float(value)) for value in values)
                or not isinstance(self.win, bool)
            ):
                raise AdapterError("valid FML score requires finite shared-evaluator metrics")
            for path_value, digest in (
                (self.artifact_path, self.artifact_sha256),
                (self.agent_result_path, self.agent_result_sha256),
                (self.evaluation_record_path, self.evaluation_record_sha256),
            ):
                path = Path(path_value)
                if (
                    not path.is_file()
                    or path.is_symlink()
                    or len(digest) != 64
                    or sha256_file(path) != digest
                ):
                    raise AdapterError("FML task evidence differs from its hash")
        elif not self.failure_reason:
            raise AdapterError("invalid FML score requires failure evidence")
        if self.internal_rounds_completed < 0 or self.internal_proposals_completed < 0:
            raise AdapterError("FML internal counters cannot be negative")
        if self.development_evaluator_calls < 0:
            raise AdapterError("FML evaluator call count cannot be negative")
        if self.token_usage is not None and any(
            not isinstance(value, int) or value < 0 for value in self.token_usage.values()
        ):
            raise AdapterError("FML token telemetry must contain non-negative integers")
        if self.request_count is not None and self.request_count < 0:
            raise AdapterError("FML request telemetry cannot be negative")
        if self.cost is not None and (not math.isfinite(self.cost) or self.cost < 0):
            raise AdapterError("FML cost telemetry must be finite and non-negative")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["raw_score"] = self.raw_score
        return payload

    @property
    def digest(self) -> str:
        self.validate()
        return hashlib.sha256(canonical_json(self.to_dict())).hexdigest()

    def write(self, path: Path) -> str:
        self.validate()
        return write_hashed_json(
            path, self.to_dict(), digest_field="task_record_digest"
        )


__all__ = ["FMLTaskRecord"]
