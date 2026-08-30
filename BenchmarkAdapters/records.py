"""Immutable run manifests and normalized benchmark results."""

from __future__ import annotations

import hashlib
import math
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
    # An Agent that spent its whole budget and was stopped by the harness, but
    # had already written a submission the official grader accepted. Spending
    # the budget is what a search-shaped Agent is supposed to do, so the work
    # counts; the status still says the Agent did not finish on its own, which
    # "completed" would hide.
    TIMED_OUT_SCORED = "timed_out_scored"
    INVALID_ARTIFACT = "invalid_artifact"
    INFRASTRUCTURE_ERROR = "infrastructure_error"

    @property
    def is_scored(self) -> bool:
        """Whether this status carries an official score."""
        return self in {RunStatus.COMPLETED, RunStatus.TIMED_OUT_SCORED}


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
    reasoning_effort: str | None
    temperature: float | None
    wall_clock_seconds: int
    asset_digests: Mapping[str, str]
    hardware: Mapping[str, Any]
    policies: Mapping[str, str]
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    formal: bool = True
    schema_version: int = 1
    benchmark_id: str | None = None
    protocol_version: str | None = None
    agent_variant: str | None = None
    benchmark_commit: str | None = None
    model_track_id: str | None = None
    outer_model_id: str | None = None
    terminal_inner_model_id: str | None = None
    relay_base_url: str | None = None
    model_parameters: Mapping[str, Any] = field(default_factory=dict)
    outer_repetitions: int | None = None
    outer_run_index: int | None = None
    development_seeds: tuple[int, ...] = ()
    heldout_seeds: tuple[int, ...] = ()
    gpu_type: str | None = None
    gpus_per_evaluation: int | None = None
    max_concurrent_evaluations: int | None = None
    task_spec_sha256: str | None = None
    allowed_write_paths: tuple[str, ...] = ()
    model_config_digest: str | None = None
    non_comparable: bool = False

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
        if self.schema_version not in {1, 2}:
            raise AdapterError("unsupported run manifest schema")
        if self.schema_version == 2:
            from .formal_contract import FormalRunContract, assert_no_secrets

            FormalRunContract(
                schema_version=self.schema_version,
                benchmark_id=self.benchmark_id or "",
                protocol_version=self.protocol_version or "",
                agent_id=self.agent,
                agent_variant=self.agent_variant or "",
                agent_source_commit=self.agent_commit,
                adapter_source_commit=self.adapter_commit,
                benchmark_source_commit=self.benchmark_commit or "",
                model_track_id=self.model_track_id or "",
                outer_model_id=self.outer_model_id or "",
                terminal_inner_model_id=self.terminal_inner_model_id,
                relay_base_url=self.relay_base_url or "",
                wall_clock_seconds=self.wall_clock_seconds,
                outer_repetitions=self.outer_repetitions or 0,
                outer_run_index=(
                    -1 if self.outer_run_index is None else self.outer_run_index
                ),
                development_seeds=self.development_seeds,
                heldout_seeds=self.heldout_seeds,
                gpu_type=self.gpu_type or "",
                gpus_per_evaluation=self.gpus_per_evaluation or 0,
                max_concurrent_evaluations=self.max_concurrent_evaluations or 0,
                task_spec_sha256=self.task_spec_sha256 or "",
                protocol_asset_digests=self.asset_digests,
                allowed_write_paths=self.allowed_write_paths,
                formal=self.formal,
                model_config_digest=self.model_config_digest,
            ).validate()
            assert_no_secrets(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        from .formal_contract import redact_relay_url

        payload = asdict(self)
        payload["mode"] = self.mode.value
        payload["asset_digests"] = dict(sorted(self.asset_digests.items()))
        payload["hardware"] = dict(sorted(self.hardware.items()))
        payload["policies"] = dict(sorted(self.policies.items()))
        payload["development_seeds"] = list(self.development_seeds)
        payload["heldout_seeds"] = list(self.heldout_seeds)
        payload["allowed_write_paths"] = list(self.allowed_write_paths)
        payload["model_parameters"] = dict(sorted(self.model_parameters.items()))
        if self.relay_base_url:
            payload["relay_base_url"] = redact_relay_url(self.relay_base_url)
        payload["non_formal"] = not self.formal
        payload["non_comparable"] = self.non_comparable or not self.formal
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
    tokens: Mapping[str, int | None] = field(default_factory=dict)
    cost: Mapping[str, float | None] = field(default_factory=dict)
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
        if self.status.is_scored and not self.score_valid:
            raise AdapterError(f"{self.status.value} result must contain a valid score")
        if self.status is not RunStatus.COMPLETED and not self.failure_reason:
            # A rescued cell keeps its reason too: it says which deadline stopped
            # the Agent, which is the whole difference from a clean completion.
            raise AdapterError("non-completed result requires a failure reason")
        if self.wall_clock_seconds < 0:
            raise AdapterError("result wall-clock cannot be negative")
        if any(value is not None and (not isinstance(value, int) or value < 0) for value in self.tokens.values()):
            raise AdapterError("result token telemetry must be unavailable or a non-negative integer")
        if any(
            value is not None
            and (not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0)
            for value in self.cost.values()
        ):
            raise AdapterError("result cost telemetry must be unavailable or finite and non-negative")

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
