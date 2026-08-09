"""Shared fail-closed contract for same-model formal benchmark runs."""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit, urlunsplit

from .contracts import AdapterError
from .protocol import canonical_json, sha256_file, write_json_exclusive


PLACEHOLDER_MARKERS = (
    "<",
    ">",
    "changeme",
    "placeholder",
    "required",
    "tbd",
    "todo",
    "unknown",
    "unset",
)
SENSITIVE_MARKERS = (
    "api_key",
    "apikey",
    "authorization",
    "bearer",
    "credential",
    "password",
    "secret",
    "token",
)
SAFE_NON_SECRET_FIELDS = {
    "input_tokens",
    "max_output_tokens",
    "max_tokens",
    "output_tokens",
    "token_budget",
    "token_usage",
    "cache_tokens",
    "reasoning_tokens",
    "total_tokens",
}


def is_placeholder(value: str | None) -> bool:
    normalized = (value or "").strip().lower()
    return not normalized or any(marker in normalized for marker in PLACEHOLDER_MARKERS)


def _contains_placeholder_value(payload: object) -> bool:
    if isinstance(payload, Mapping):
        return any(_contains_placeholder_value(value) for value in payload.values())
    if isinstance(payload, (list, tuple)):
        return any(_contains_placeholder_value(value) for value in payload)
    return isinstance(payload, str) and is_placeholder(payload)


def assert_no_secrets(payload: object, path: str = "record") -> None:
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            normalized = str(key).lower().replace("-", "_")
            safe_telemetry = normalized in SAFE_NON_SECRET_FIELDS or normalized.endswith("_tokens")
            if not safe_telemetry and any(
                marker in normalized for marker in SENSITIVE_MARKERS
            ):
                raise AdapterError(f"{path} contains a prohibited secret field: {key}")
            assert_no_secrets(value, f"{path}.{key}")
    elif isinstance(payload, (list, tuple)):
        for index, value in enumerate(payload):
            assert_no_secrets(value, f"{path}[{index}]")
    elif isinstance(payload, str):
        normalized = payload.lower()
        if "authorization:" in normalized or "bearer " in normalized:
            raise AdapterError(f"{path} contains an authorization value")


def redact_relay_url(value: str) -> str:
    parts = urlsplit(value)
    host = parts.hostname or ""
    if parts.port is not None:
        host = f"{host}:{parts.port}"
    return urlunsplit((parts.scheme, host, parts.path.rstrip("/"), "", ""))


def hardware_comparison_fingerprint(hardware: Mapping[str, Any]) -> str:
    """Hash a comparable hardware profile while retaining per-run allocation evidence elsewhere."""

    allocation_fields = {
        "agent_runtime_fingerprint",
        "gpu_id",
        "gpu_ids",
        "gpu_memory_used_mb_before_run",
        "gpu_uuid",
        "gpus",
    }
    comparable = {
        str(name): value
        for name, value in hardware.items()
        if name not in allocation_fields
    }
    raw_gpus = hardware.get("gpus")
    if isinstance(raw_gpus, list):
        profiles = []
        for gpu in raw_gpus:
            if not isinstance(gpu, Mapping):
                raise AdapterError("hardware GPU profile must be an object")
            profiles.append(
                {
                    str(name): value
                    for name, value in gpu.items()
                    if name not in {"gpu_id", "gpu_uuid"}
                }
            )
        comparable["gpu_profiles"] = sorted(
            profiles,
            key=lambda profile: canonical_json(profile),
        )
    return hashlib.sha256(canonical_json(comparable)).hexdigest()


def validate_gpu_attestation(
    hardware: Mapping[str, Any],
    *,
    expected_type: str,
    gpus_per_evaluation: int,
    max_concurrent_evaluations: int,
) -> None:
    expected_count = gpus_per_evaluation * max_concurrent_evaluations
    gpu_ids = hardware.get("gpu_ids")
    gpus = hardware.get("gpus")
    if not (
        hardware.get("gpu_type") == expected_type
        and hardware.get("gpu_count") == expected_count
        and hardware.get("gpus_per_evaluation") == gpus_per_evaluation
        and hardware.get("max_concurrent_evaluations") == max_concurrent_evaluations
        and hardware.get("gpu_exclusivity") == "verified-and-host-locked"
        and isinstance(gpu_ids, list)
        and len(gpu_ids) == expected_count
        and len(set(str(value) for value in gpu_ids)) == expected_count
        and isinstance(gpus, list)
        and len(gpus) == expected_count
    ):
        raise AdapterError("formal GPU allocation attestation is incomplete")
    attested_ids: set[str] = set()
    attested_uuids: set[str] = set()
    for gpu in gpus:
        if not isinstance(gpu, Mapping):
            raise AdapterError("formal GPU allocation entry must be an object")
        gpu_id = str(gpu.get("gpu_id", ""))
        uuid = str(gpu.get("gpu_uuid", ""))
        name = str(gpu.get("gpu_name", ""))
        memory = gpu.get("gpu_memory_total_mb")
        if (
            gpu_id not in {str(value) for value in gpu_ids}
            or not uuid
            or expected_type.lower() not in name.lower()
            or not isinstance(memory, int)
            or memory < 1
        ):
            raise AdapterError("formal GPU allocation entry differs from its profile")
        attested_ids.add(gpu_id)
        attested_uuids.add(uuid)
    if len(attested_ids) != expected_count or len(attested_uuids) != expected_count:
        raise AdapterError("formal GPU allocation contains duplicate IDs or UUIDs")


@dataclass(frozen=True)
class ModelTrackConfig:
    schema_version: int
    model_track_id: str
    outer_model_id: str
    relay_base_url: str
    model_parameters: Mapping[str, Any]
    terminal_inner_model_id: str | None = None
    terminal_inner_parameters: Mapping[str, Any] = field(default_factory=dict)
    request_timeout_seconds: int | None = None
    retry_policy: Mapping[str, Any] = field(default_factory=dict)

    def validate(self, *, formal: bool, require_terminal_inner: bool = False) -> None:
        if self.schema_version != 1:
            raise AdapterError("unsupported model track schema")
        required = {
            "model_track_id": self.model_track_id,
            "outer_model_id": self.outer_model_id,
            "relay_base_url": self.relay_base_url,
        }
        if require_terminal_inner:
            required["terminal_inner_model_id"] = self.terminal_inner_model_id or ""
        if formal:
            missing = [name for name, value in required.items() if is_placeholder(value)]
            if missing:
                raise AdapterError(
                    "formal model configuration is incomplete or contains placeholders: "
                    + ", ".join(sorted(missing))
                )
            if not self.model_parameters:
                raise AdapterError("formal model configuration requires explicit outer parameters")
            if require_terminal_inner and not self.terminal_inner_parameters:
                raise AdapterError("formal Terminal AO requires explicit inner model parameters")
            if _contains_placeholder_value(self.model_parameters) or _contains_placeholder_value(
                self.terminal_inner_parameters
            ):
                raise AdapterError("formal model parameters contain placeholder values")
            if require_terminal_inner and self.request_timeout_seconds is None:
                raise AdapterError("formal Terminal AO requires an explicit request timeout")
            if require_terminal_inner and not self.retry_policy:
                raise AdapterError("formal Terminal AO requires an explicit retry policy")
        if self.request_timeout_seconds is not None and self.request_timeout_seconds < 1:
            raise AdapterError("model request timeout must be positive")
        if formal:
            relay = urlsplit(self.relay_base_url)
            if relay.scheme not in {"http", "https"} or not relay.hostname:
                raise AdapterError("relay_base_url must be an absolute HTTP(S) URL")
            if relay.username is not None or relay.password is not None:
                raise AdapterError("relay_base_url must not embed credentials")
            if relay.query or relay.fragment:
                raise AdapterError("relay_base_url must not contain query parameters or fragments")
        assert_no_secrets(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "model_track_id": self.model_track_id,
            "outer_model_id": self.outer_model_id,
            "terminal_inner_model_id": self.terminal_inner_model_id,
            "relay_base_url": redact_relay_url(self.relay_base_url),
            "model_parameters": dict(sorted(self.model_parameters.items())),
            "terminal_inner_parameters": dict(sorted(self.terminal_inner_parameters.items())),
            "request_timeout_seconds": self.request_timeout_seconds,
            "retry_policy": dict(sorted(self.retry_policy.items())),
        }

    @property
    def digest(self) -> str:
        return hashlib.sha256(canonical_json(self.to_dict())).hexdigest()

    @classmethod
    def load(cls, path: Path, *, formal: bool, require_terminal_inner: bool = False) -> "ModelTrackConfig":
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            config = cls(
                schema_version=int(payload["schema_version"]),
                model_track_id=str(payload["model_track_id"]),
                outer_model_id=str(payload["outer_model_id"]),
                terminal_inner_model_id=(
                    None
                    if payload.get("terminal_inner_model_id") is None
                    else str(payload["terminal_inner_model_id"])
                ),
                relay_base_url=str(payload["relay_base_url"]),
                model_parameters=dict(payload.get("model_parameters", {})),
                terminal_inner_parameters=dict(payload.get("terminal_inner_parameters", {})),
                request_timeout_seconds=(
                    None
                    if payload.get("request_timeout_seconds") is None
                    else int(payload["request_timeout_seconds"])
                ),
                retry_policy=dict(payload.get("retry_policy", {})),
            )
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise AdapterError(f"invalid model track configuration: {path}") from exc
        config.validate(formal=formal, require_terminal_inner=require_terminal_inner)
        return config


@dataclass(frozen=True)
class FormalRunContract:
    schema_version: int
    benchmark_id: str
    protocol_version: str
    agent_id: str
    agent_variant: str
    agent_source_commit: str
    adapter_source_commit: str
    benchmark_source_commit: str
    model_track_id: str
    outer_model_id: str
    relay_base_url: str
    wall_clock_seconds: int
    outer_repetitions: int
    outer_run_index: int
    development_seeds: tuple[int, ...]
    heldout_seeds: tuple[int, ...]
    gpu_type: str
    gpus_per_evaluation: int
    max_concurrent_evaluations: int
    task_spec_sha256: str
    protocol_asset_digests: Mapping[str, str]
    allowed_write_paths: tuple[str, ...]
    formal: bool
    terminal_inner_model_id: str | None = None
    model_config_digest: str | None = None

    def validate(self) -> None:
        if self.schema_version != 2:
            raise AdapterError("formal run contract requires schema_version 2")
        for name, value in {
            "benchmark_id": self.benchmark_id,
            "protocol_version": self.protocol_version,
            "agent_id": self.agent_id,
            "agent_variant": self.agent_variant,
            "model_track_id": self.model_track_id,
            "outer_model_id": self.outer_model_id,
            "relay_base_url": self.relay_base_url,
            "gpu_type": self.gpu_type,
        }.items():
            if not str(value).strip():
                raise AdapterError(f"formal run contract field is empty: {name}")
        if self.formal:
            relay = urlsplit(self.relay_base_url)
            if (
                relay.scheme not in {"http", "https"}
                or not relay.hostname
                or relay.username is not None
                or relay.password is not None
                or relay.query
                or relay.fragment
            ):
                raise AdapterError(
                    "formal run relay URL must be an absolute credential-free HTTP(S) URL"
                )
            placeholders = [
                name
                for name, value in {
                    "agent_variant": self.agent_variant,
                    "model_track_id": self.model_track_id,
                    "outer_model_id": self.outer_model_id,
                    "relay_base_url": self.relay_base_url,
                    "gpu_type": self.gpu_type,
                }.items()
                if is_placeholder(str(value))
            ]
            if placeholders:
                raise AdapterError(
                    "formal run contract contains placeholders: " + ", ".join(sorted(placeholders))
                )
            if self.agent_variant.strip().lower() == "default":
                raise AdapterError("formal run requires an explicit non-default Agent variant")
            if self.model_config_digest is None:
                raise AdapterError("formal run requires an immutable model configuration digest")
            if self.benchmark_id == "terminal-bench-ao" and not self.terminal_inner_model_id:
                raise AdapterError("formal Terminal AO requires an explicit inner model ID")
        for name, commit in {
            "agent_source_commit": self.agent_source_commit,
            "adapter_source_commit": self.adapter_source_commit,
            "benchmark_source_commit": self.benchmark_source_commit,
        }.items():
            if len(commit) != 40 or any(
                character not in "0123456789abcdef" for character in commit.lower()
            ):
                raise AdapterError(f"formal run contract requires an immutable commit: {name}")
        if self.wall_clock_seconds < 1:
            raise AdapterError("formal run wall-clock must be positive")
        if self.outer_repetitions not in {1, 3}:
            raise AdapterError("outer_repetitions must be 1 or 3")
        if not 0 <= self.outer_run_index < self.outer_repetitions:
            raise AdapterError("outer_run_index is outside configured repetitions")
        if self.gpus_per_evaluation < 1 or self.max_concurrent_evaluations < 1:
            raise AdapterError("GPU allocation fields must be positive")
        if (
            len(self.task_spec_sha256) != 64
            or any(character not in "0123456789abcdef" for character in self.task_spec_sha256.lower())
            or not self.protocol_asset_digests
        ):
            raise AdapterError("formal run requires a frozen task specification and protocol assets")
        if not self.allowed_write_paths:
            raise AdapterError("formal run requires explicit allowed write paths")
        for value in self.allowed_write_paths:
            path = Path(value)
            if path.is_absolute() or not path.parts or ".." in path.parts:
                raise AdapterError(f"formal run allowed write path is unsafe: {value}")
        for name, digest in self.protocol_asset_digests.items():
            if not name or len(digest) != 64 or any(
                character not in "0123456789abcdef" for character in digest.lower()
            ):
                raise AdapterError(f"invalid formal protocol asset digest: {name}")
        if self.model_config_digest is not None and (
            len(self.model_config_digest) != 64
            or any(
                character not in "0123456789abcdef"
                for character in self.model_config_digest.lower()
            )
        ):
            raise AdapterError("invalid model configuration digest")
        assert_no_secrets(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["relay_base_url"] = redact_relay_url(self.relay_base_url)
        payload["development_seeds"] = list(self.development_seeds)
        payload["heldout_seeds"] = list(self.heldout_seeds)
        payload["allowed_write_paths"] = list(self.allowed_write_paths)
        payload["protocol_asset_digests"] = dict(sorted(self.protocol_asset_digests.items()))
        payload["run_mode"] = "formal" if self.formal else "smoke"
        payload["non_formal"] = not self.formal
        payload["non_comparable"] = not self.formal
        return payload


@dataclass(frozen=True)
class PreflightCheck:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class FormalPreflightReport:
    schema_version: int
    benchmark_id: str
    formal: bool
    checks: tuple[PreflightCheck, ...]

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)

    def require_ready(self) -> None:
        if self.formal and not self.passed:
            failed = [check.name for check in self.checks if not check.passed]
            raise AdapterError("formal preflight failed: " + ", ".join(failed))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "benchmark_id": self.benchmark_id,
            "formal": self.formal,
            "non_formal": not self.formal,
            "non_comparable": not self.formal,
            "passed": self.passed,
            "checks": [asdict(check) for check in self.checks],
            **{check.name: check.passed for check in self.checks},
        }

    def write(self, path: Path) -> None:
        write_json_exclusive(path, self.to_dict())


def repetition_summary(values: Sequence[float], *, outer_repetitions: int) -> dict[str, Any]:
    if len(values) != outer_repetitions:
        raise AdapterError(
            f"aggregate requires all {outer_repetitions} configured outer runs; found {len(values)}"
        )
    if outer_repetitions not in {1, 3}:
        raise AdapterError("outer_repetitions must be 1 or 3")
    if any(not math.isfinite(float(value)) for value in values):
        raise AdapterError("aggregate received a non-finite score")
    mean = statistics.fmean(float(value) for value in values)
    if outer_repetitions == 1:
        return {
            "reporting_label": "single_run",
            "num_outer_runs": 1,
            "mean": mean,
            "standard_deviation": None,
            "standard_error": None,
            "ci95_lower": None,
            "ci95_upper": None,
        }
    standard_deviation = statistics.stdev(float(value) for value in values)
    standard_error = standard_deviation / math.sqrt(outer_repetitions)
    margin = 1.96 * standard_error
    return {
        "reporting_label": "avg_at_3",
        "num_outer_runs": 3,
        "mean": mean,
        "standard_deviation": standard_deviation,
        "standard_error": standard_error,
        "ci95_lower": mean - margin,
        "ci95_upper": mean + margin,
    }


def verify_hashed_json(path: Path, *, digest_field: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AdapterError(f"invalid immutable JSON record: {path}") from exc
    expected = payload.pop(digest_field, None)
    actual = hashlib.sha256(canonical_json(payload)).hexdigest()
    if expected != actual:
        raise AdapterError(f"immutable JSON record digest mismatch: {path}")
    payload[digest_field] = expected
    return payload


def write_hashed_json(path: Path, payload: Mapping[str, Any], *, digest_field: str) -> str:
    assert_no_secrets(payload)
    digest = hashlib.sha256(canonical_json(payload)).hexdigest()
    write_json_exclusive(path, {**payload, digest_field: digest})
    return digest


def verify_file_digest(path: Path, expected: str, description: str) -> None:
    if not path.is_file() or path.is_symlink() or sha256_file(path) != expected:
        raise AdapterError(f"{description} differs from its frozen digest")


__all__ = [
    "FormalPreflightReport",
    "FormalRunContract",
    "ModelTrackConfig",
    "PreflightCheck",
    "assert_no_secrets",
    "hardware_comparison_fingerprint",
    "is_placeholder",
    "redact_relay_url",
    "repetition_summary",
    "validate_gpu_attestation",
    "verify_file_digest",
    "verify_hashed_json",
    "write_hashed_json",
]
