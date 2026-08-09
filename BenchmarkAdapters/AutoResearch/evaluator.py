"""Host-owned candidate execution and strict Autoresearch metric parsing."""

from __future__ import annotations

import json
import hashlib
import math
import os
import re
import shutil
import signal
import subprocess
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from pathlib import PurePosixPath
from typing import Mapping, Sequence

from ..contracts import AdapterError
from ..protocol import canonical_json, sha256_file, write_json_exclusive
from ..registry import ROOT
from .baseline import KernelCacheManifest, PreparedAssetManifest
from .revisions import TrainRevisionStore
from .seed_injection import inject_seed, validate_candidate_policy


_METRIC_LINE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9_]*)\s*:\s*(\S+)\s*$")
_HOST_VAL_BPB_LINE = re.compile(r"^AUTORESEARCH_HOST_VAL_BPB:([^\s]+)$")
_OOM_MARKERS = (
    "cuda out of memory",
    "outofmemoryerror",
    "cublas_status_alloc_failed",
    "hip out of memory",
)


@dataclass(frozen=True)
class EvaluatorManifest:
    schema_version: int
    candidate_hard_timeout_seconds: int
    candidate_training_seconds: int
    editable_paths: tuple[str, ...]
    metric_direction: str
    primary_metric: str
    required_metrics: tuple[str, ...]
    training_seconds_tolerance: tuple[float, float]
    implementation_files: Mapping[str, str]

    @classmethod
    def load(cls, path: Path) -> "EvaluatorManifest":
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            manifest = cls(
                schema_version=int(payload["schema_version"]),
                candidate_hard_timeout_seconds=int(payload["candidate_hard_timeout_seconds"]),
                candidate_training_seconds=int(payload["candidate_training_seconds"]),
                editable_paths=tuple(payload["editable_paths"]),
                metric_direction=str(payload["metric_direction"]),
                primary_metric=str(payload["primary_metric"]),
                required_metrics=tuple(payload["required_metrics"]),
                training_seconds_tolerance=tuple(
                    float(value) for value in payload["training_seconds_tolerance"]
                ),
                implementation_files=dict(payload["implementation_files"]),
            )
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise AdapterError(f"invalid Autoresearch evaluator manifest: {path}") from exc
        manifest.validate()
        return manifest

    def validate(self) -> None:
        expected_metrics = {
            "val_bpb",
            "training_seconds",
            "total_seconds",
            "peak_vram_mb",
            "mfu_percent",
            "total_tokens_M",
            "num_steps",
            "num_params_M",
            "depth",
        }
        if self.schema_version != 1:
            raise AdapterError("unsupported Autoresearch evaluator schema")
        if self.candidate_training_seconds != 300:
            raise AdapterError("Autoresearch evaluator training budget must be 300 seconds")
        if self.candidate_hard_timeout_seconds not in range(300, 901):
            raise AdapterError("Autoresearch evaluator timeout must be between 300 and 900 seconds")
        if self.editable_paths != ("train.py",):
            raise AdapterError("Autoresearch evaluator editable path must be train.py only")
        if self.metric_direction != "minimize" or self.primary_metric != "val_bpb":
            raise AdapterError("Autoresearch evaluator must minimize val_bpb")
        if set(self.required_metrics) != expected_metrics or len(self.required_metrics) != len(
            expected_metrics
        ):
            raise AdapterError("Autoresearch evaluator required metric set differs from implementation")
        if len(self.training_seconds_tolerance) != 2:
            raise AdapterError("Autoresearch evaluator training tolerance is invalid")
        low, high = self.training_seconds_tolerance
        if not (0 < low <= 300 <= high):
            raise AdapterError("Autoresearch evaluator training tolerance excludes 300 seconds")
        expected_implementation_files = {
            "BenchmarkAdapters/AutoResearch/aggregate.py",
            "BenchmarkAdapters/AutoResearch/baseline.py",
            "BenchmarkAdapters/AutoResearch/broker.py",
            "BenchmarkAdapters/AutoResearch/evaluator.py",
            "BenchmarkAdapters/AutoResearch/protocol.py",
            "BenchmarkAdapters/AutoResearch/revisions.py",
            "BenchmarkAdapters/AutoResearch/seed_injection.py",
            "BenchmarkAdapters/AutoResearch/supervisor.py",
        }
        if set(self.implementation_files) != expected_implementation_files:
            raise AdapterError("Autoresearch evaluator implementation file set differs from protocol")
        for relative, expected_digest in self.implementation_files.items():
            relative_path = PurePosixPath(relative)
            if relative_path.is_absolute() or ".." in relative_path.parts:
                raise AdapterError(f"unsafe Autoresearch evaluator implementation path: {relative}")
            path = ROOT / relative
            if not path.is_file() or path.is_symlink() or sha256_file(path) != expected_digest:
                raise AdapterError(f"Autoresearch evaluator implementation drift: {relative}")


def parse_metrics(stdout: str, manifest: EvaluatorManifest) -> dict[str, float | int]:
    """Parse one and only one finite value for every frozen summary metric."""

    values: dict[str, float] = {}
    required = set(manifest.required_metrics)
    for line in stdout.splitlines():
        match = _METRIC_LINE.fullmatch(line)
        if match is None or match.group(1) not in required:
            continue
        key, raw_value = match.groups()
        if key in values:
            raise AdapterError(f"duplicate Autoresearch metric: {key}")
        try:
            value = float(raw_value)
        except ValueError as exc:
            raise AdapterError(f"non-numeric Autoresearch metric: {key}") from exc
        if not math.isfinite(value):
            raise AdapterError(f"non-finite Autoresearch metric: {key}")
        values[key] = value
    missing = sorted(required - set(values))
    if missing:
        raise AdapterError(f"missing Autoresearch metrics: {missing}")
    if values["val_bpb"] <= 0:
        raise AdapterError("Autoresearch val_bpb must be positive")
    for key in required - {"mfu_percent"}:
        if values[key] < 0:
            raise AdapterError(f"Autoresearch metric must be non-negative: {key}")
    low, high = manifest.training_seconds_tolerance
    if not low <= values["training_seconds"] <= high:
        raise AdapterError("Autoresearch training_seconds is outside the frozen tolerance")
    normalized: dict[str, float | int] = dict(values)
    for key in ("num_steps", "depth"):
        if not values[key].is_integer():
            raise AdapterError(f"Autoresearch metric must be an integer: {key}")
        normalized[key] = int(values[key])
    return normalized


def parse_attested_val_bpb(stdout: str) -> float:
    values: list[float] = []
    for line in stdout.splitlines():
        match = _HOST_VAL_BPB_LINE.fullmatch(line.strip())
        if match is None:
            continue
        try:
            value = float(match.group(1))
        except ValueError as exc:
            raise AdapterError("host-attested Autoresearch val_bpb is not numeric") from exc
        if not math.isfinite(value) or value <= 0:
            raise AdapterError("host-attested Autoresearch val_bpb must be finite and positive")
        values.append(value)
    if len(values) != 1:
        raise AdapterError("Autoresearch evaluator requires exactly one host-attested val_bpb")
    return values[0]


class EvaluationStatus(str, Enum):
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    OOM = "oom"
    INVALID_METRICS = "invalid_metrics"
    INVALID_ARTIFACT = "invalid_artifact"
    INFRASTRUCTURE_ERROR = "infrastructure_error"


@dataclass(frozen=True)
class CandidateEvaluation:
    evaluation_id: str
    revision_id: str
    candidate_sha256: str
    seed: int
    status: EvaluationStatus
    score_valid: bool
    val_bpb: float | None
    metrics: Mapping[str, float | int]
    return_code: int | None
    timed_out: bool
    wall_clock_seconds: float
    command: tuple[str, ...]
    executed_train_sha256: str | None
    stdout_sha256: str | None
    stderr_sha256: str | None
    failure_reason: str | None = None
    parent_revision_id: str | None = None
    agent: str | None = None
    outer_seed: int | None = None
    candidate_sequence: int | None = None
    evaluator_digest: str | None = None
    environment_digest: str | None = None
    gpu_id: str | None = None
    gpu_uuid: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    schema_version: int = 2
    protocol_digest: str | None = None
    benchmark_commit: str | None = None
    evaluator_version: str = "autoresearch-evaluator-v2"

    def validate(self) -> None:
        if self.schema_version != 2:
            raise AdapterError("unsupported Autoresearch evaluation schema")
        if self.evaluator_version != "autoresearch-evaluator-v2":
            raise AdapterError("unsupported Autoresearch evaluator version")
        if not self.evaluation_id or not self.revision_id or len(self.candidate_sha256) != 64:
            raise AdapterError("Autoresearch evaluation identity is invalid")
        if self.seed < 0 or self.wall_clock_seconds < 0:
            raise AdapterError("Autoresearch evaluation seed or duration is invalid")
        for name, digest in (
            ("executed_train", self.executed_train_sha256),
            ("stdout", self.stdout_sha256),
            ("stderr", self.stderr_sha256),
            ("evaluator", self.evaluator_digest),
            ("environment", self.environment_digest),
        ):
            if digest is not None and (
                len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest)
            ):
                raise AdapterError(f"Autoresearch evaluation {name} digest is invalid")
        if self.score_valid:
            if self.status is not EvaluationStatus.COMPLETED or self.val_bpb is None:
                raise AdapterError("valid Autoresearch evaluation must be completed with val_bpb")
            if not math.isfinite(self.val_bpb):
                raise AdapterError("valid Autoresearch val_bpb must be finite")
        elif not self.failure_reason:
            raise AdapterError("invalid Autoresearch evaluation requires a failure reason")

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["status"] = self.status.value
        payload["metrics"] = dict(self.metrics)
        payload["command"] = list(self.command)
        return payload

    @property
    def digest(self) -> str:
        return hashlib.sha256(canonical_json(self.to_dict())).hexdigest()


class CandidateEvaluator:
    """Executes a revision in a host-owned evaluation copy."""

    def __init__(
        self,
        *,
        manifest: EvaluatorManifest,
        prepared_root: Path,
        command_prefix: Sequence[str] = ("uv", "run", "--frozen", "--offline"),
        timeout_seconds: int | None = None,
        environment: Mapping[str, str] | None = None,
        gpu_id: str | None = None,
        gpu_uuid: str | None = None,
        prepared_manifest: PreparedAssetManifest | None = None,
        kernel_cache_root: Path | None = None,
        kernel_cache_manifest: KernelCacheManifest | None = None,
        sandbox: bool = False,
        enforce_wall_clock_budget: bool = False,
        attest_evaluate_bpb: bool = False,
        protocol_digest: str | None = None,
        benchmark_commit: str | None = None,
    ) -> None:
        manifest.validate()
        self.manifest = manifest
        self.prepared_root = prepared_root.resolve()
        if not self.prepared_root.is_dir():
            raise AdapterError(f"Autoresearch prepared root does not exist: {self.prepared_root}")
        if not command_prefix:
            raise AdapterError("Autoresearch evaluator command prefix must not be empty")
        self.command_prefix = tuple(command_prefix)
        executable = self.command_prefix[0]
        if "/" in executable:
            executable_path = Path(executable).expanduser().resolve()
            if not executable_path.is_file() or not os.access(executable_path, os.X_OK):
                raise AdapterError(f"Autoresearch locked evaluator executable is unavailable: {executable_path}")
        elif shutil.which(executable) is None:
            raise AdapterError(f"Autoresearch evaluator executable is unavailable: {executable}")
        self.timeout_seconds = timeout_seconds or manifest.candidate_hard_timeout_seconds
        if self.timeout_seconds < 1 or self.timeout_seconds > manifest.candidate_hard_timeout_seconds:
            raise AdapterError("Autoresearch evaluator timeout exceeds the frozen hard timeout")
        self.environment = dict(environment or {})
        self.gpu_id = gpu_id
        self.gpu_uuid = gpu_uuid
        if sandbox and (gpu_id is None or not gpu_id.isdigit()):
            raise AdapterError("Autoresearch sandbox evaluator requires a numeric GPU index")
        self.prepared_manifest = prepared_manifest
        self.kernel_cache_root = kernel_cache_root.resolve() if kernel_cache_root else None
        self.kernel_cache_manifest = kernel_cache_manifest
        if (self.kernel_cache_root is None) != (self.kernel_cache_manifest is None):
            raise AdapterError("Autoresearch kernel cache root and manifest must be configured together")
        if self.kernel_cache_manifest is not None:
            self.kernel_cache_manifest.validate(self.kernel_cache_root)
        self.sandbox = sandbox
        self.enforce_wall_clock_budget = enforce_wall_clock_budget
        self.attest_evaluate_bpb = attest_evaluate_bpb
        self.protocol_digest = protocol_digest
        self.benchmark_commit = benchmark_commit
        if sandbox and shutil.which("bwrap") is None:
            raise AdapterError("Autoresearch formal evaluator requires bubblewrap isolation")

    @property
    def evaluator_digest(self) -> str:
        return hashlib.sha256(canonical_json(asdict(self.manifest))).hexdigest()

    @property
    def environment_digest(self) -> str:
        payload = {
            "command_prefix": list(self.command_prefix),
            "environment": dict(sorted(self.environment.items())),
            "prepared_manifest": self.prepared_manifest.digest if self.prepared_manifest else None,
            "kernel_cache_manifest": self.kernel_cache_manifest.digest if self.kernel_cache_manifest else None,
            "sandbox": self.sandbox,
            "gpu_id": self.gpu_id,
            "gpu_uuid": self.gpu_uuid,
            "protocol_digest": self.protocol_digest,
            "benchmark_commit": self.benchmark_commit,
        }
        return hashlib.sha256(canonical_json(payload)).hexdigest()

    def evaluate(
        self,
        *,
        store: TrainRevisionStore,
        revision_id: str,
        seed: int,
        output_dir: Path,
        evaluation_id: str,
        agent: str | None = None,
        outer_seed: int | None = None,
        candidate_sequence: int | None = None,
    ) -> CandidateEvaluation:
        output_dir = output_dir.resolve()
        if output_dir.exists() or output_dir.is_symlink():
            raise AdapterError(f"Autoresearch evaluation output already exists: {output_dir}")
        output_dir.mkdir(parents=True)
        revision = store.get(revision_id)
        audit = {
            "parent_revision_id": revision.parent_id,
            "agent": agent,
            "outer_seed": outer_seed,
            "candidate_sequence": candidate_sequence,
            "evaluator_digest": self.evaluator_digest,
            "environment_digest": self.environment_digest,
            "gpu_id": self.gpu_id,
            "gpu_uuid": self.gpu_uuid,
        }
        evaluation_started_at = datetime.now(timezone.utc).isoformat()
        workspace = store.replay(revision_id, output_dir / "workspace")
        if self.prepared_manifest is not None:
            self.prepared_manifest.validate(self.prepared_root)
        if self.kernel_cache_manifest is not None:
            self.kernel_cache_manifest.validate(self.kernel_cache_root)
        artifact_before = sha256_file(revision.path / "train.py")
        try:
            candidate_source = (workspace / "train.py").read_text(encoding="utf-8")
            if self.attest_evaluate_bpb:
                validate_candidate_policy(candidate_source)
            transformed = inject_seed(candidate_source, seed)
            (workspace / "train.py").write_text(transformed, encoding="utf-8")
        except AdapterError as exc:
            return self._write_failure(
                output_dir=output_dir,
                evaluation_id=evaluation_id,
                revision_id=revision_id,
                candidate_sha256=revision.train_sha256,
                seed=seed,
                status=EvaluationStatus.INVALID_ARTIFACT,
                command=(*self.command_prefix, "train.py"),
                failure_reason=str(exc),
                audit=audit,
                started_at=evaluation_started_at,
            )
        if artifact_before != revision.train_sha256 or sha256_file(revision.path / "train.py") != artifact_before:
            raise AdapterError("Autoresearch evaluator mutated the immutable candidate artifact")

        home = output_dir / "home"
        cache = home / ".cache"
        cache.mkdir(parents=True)
        prepared_mount = cache / "autoresearch"
        kernel_mount = cache / "huggingface/hub/models--varunneal--flash-attention-3"
        if self.sandbox:
            prepared_mount.mkdir()
            if self.kernel_cache_root is not None:
                kernel_mount.mkdir(parents=True)
        else:
            prepared_mount.symlink_to(self.prepared_root, target_is_directory=True)
            if self.kernel_cache_root is not None:
                kernel_mount.parent.mkdir(parents=True, exist_ok=True)
                kernel_mount.symlink_to(self.kernel_cache_root, target_is_directory=True)
        executed_train_sha256 = sha256_file(workspace / "train.py")
        host_runner = None
        if self.attest_evaluate_bpb:
            host_runner = output_dir / "host_runner.py"
            self._write_host_runner(host_runner)
            training_command = (*self.command_prefix, str(host_runner), "train.py")
        else:
            training_command = (*self.command_prefix, "train.py")
        command = self._sandbox_command(
            training_command=training_command,
            workspace=workspace,
            home=home,
            host_runner=host_runner,
        )
        inherited_environment = {
            key: value
            for key, value in os.environ.items()
            if key
            in {
                "CUDA_HOME",
                "CUDA_PATH",
                "LANG",
                "LC_ALL",
                "LD_LIBRARY_PATH",
                "NVIDIA_DRIVER_CAPABILITIES",
                "NVIDIA_VISIBLE_DEVICES",
                "PATH",
                "TMPDIR",
            }
        }
        environment = inherited_environment
        environment.update(
            {
                "HOME": str(home),
                "PYTHONHASHSEED": str(seed),
                "HF_HUB_OFFLINE": "1",
                "HF_HOME": str(cache / "huggingface"),
                "HF_HUB_CACHE": str(cache / "huggingface/hub"),
                "PYTHONDONTWRITEBYTECODE": "1",
                "TRANSFORMERS_OFFLINE": "1",
            }
        )
        environment.update(self.environment)
        if self.gpu_id is not None:
            environment["CUDA_VISIBLE_DEVICES"] = self.gpu_id
        started = time.monotonic()
        return_code: int | None = None
        timed_out = False
        stdout = ""
        stderr = ""
        try:
            process = subprocess.Popen(
                command,
                cwd=workspace,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
            try:
                stdout, stderr = process.communicate(timeout=self.timeout_seconds)
                return_code = process.returncode
            except subprocess.TimeoutExpired as exc:
                timed_out = True
                os.killpg(process.pid, signal.SIGKILL)
                remaining_stdout, remaining_stderr = process.communicate()
                stdout = (exc.stdout or "") + remaining_stdout
                stderr = (exc.stderr or "") + remaining_stderr
                return_code = process.returncode
        except OSError as exc:
            return self._write_failure(
                output_dir=output_dir,
                evaluation_id=evaluation_id,
                revision_id=revision_id,
                candidate_sha256=revision.train_sha256,
                seed=seed,
                status=EvaluationStatus.INFRASTRUCTURE_ERROR,
                command=command,
                failure_reason=f"{type(exc).__name__}: {exc}",
                wall_clock_seconds=time.monotonic() - started,
                audit=audit,
                started_at=evaluation_started_at,
            )
        wall_clock = time.monotonic() - started
        self._write_text_exclusive(output_dir / "stdout.log", stdout)
        self._write_text_exclusive(output_dir / "stderr.log", stderr)
        stdout_sha256 = sha256_file(output_dir / "stdout.log")
        stderr_sha256 = sha256_file(output_dir / "stderr.log")
        try:
            self._validate_protected_assets(
                store,
                workspace,
                executed_train_sha256=executed_train_sha256,
            )
            if self.prepared_manifest is not None:
                self.prepared_manifest.validate(self.prepared_root)
            if self.kernel_cache_manifest is not None:
                self.kernel_cache_manifest.validate(self.kernel_cache_root)
        except AdapterError as exc:
            return self._write_failure(
                output_dir=output_dir,
                evaluation_id=evaluation_id,
                revision_id=revision_id,
                candidate_sha256=revision.train_sha256,
                seed=seed,
                status=EvaluationStatus.INVALID_ARTIFACT,
                command=command,
                failure_reason=str(exc),
                return_code=return_code,
                timed_out=timed_out,
                wall_clock_seconds=wall_clock,
                executed_train_sha256=executed_train_sha256,
                audit=audit,
                started_at=evaluation_started_at,
            )
        if timed_out:
            return self._write_failure(
                output_dir=output_dir,
                evaluation_id=evaluation_id,
                revision_id=revision_id,
                candidate_sha256=revision.train_sha256,
                seed=seed,
                status=EvaluationStatus.TIMED_OUT,
                command=command,
                failure_reason=f"candidate exceeded {self.timeout_seconds}s hard timeout",
                return_code=return_code,
                timed_out=True,
                wall_clock_seconds=wall_clock,
                executed_train_sha256=executed_train_sha256,
                audit=audit,
                started_at=evaluation_started_at,
            )
        if return_code != 0:
            combined = (stdout + "\n" + stderr).lower()
            status = EvaluationStatus.OOM if any(marker in combined for marker in _OOM_MARKERS) else EvaluationStatus.FAILED
            return self._write_failure(
                output_dir=output_dir,
                evaluation_id=evaluation_id,
                revision_id=revision_id,
                candidate_sha256=revision.train_sha256,
                seed=seed,
                status=status,
                command=command,
                failure_reason=f"candidate exited with code {return_code}",
                return_code=return_code,
                wall_clock_seconds=wall_clock,
                executed_train_sha256=executed_train_sha256,
                audit=audit,
                started_at=evaluation_started_at,
            )
        try:
            metrics = parse_metrics(stdout, self.manifest)
        except AdapterError as exc:
            return self._write_failure(
                output_dir=output_dir,
                evaluation_id=evaluation_id,
                revision_id=revision_id,
                candidate_sha256=revision.train_sha256,
                seed=seed,
                status=EvaluationStatus.INVALID_METRICS,
                command=command,
                failure_reason=str(exc),
                return_code=return_code,
                wall_clock_seconds=wall_clock,
                executed_train_sha256=executed_train_sha256,
                audit=audit,
                started_at=evaluation_started_at,
            )
        if self.attest_evaluate_bpb:
            try:
                attested_val_bpb = parse_attested_val_bpb(stdout)
            except AdapterError as exc:
                return self._write_failure(
                    output_dir=output_dir,
                    evaluation_id=evaluation_id,
                    revision_id=revision_id,
                    candidate_sha256=revision.train_sha256,
                    seed=seed,
                    status=EvaluationStatus.INVALID_METRICS,
                    command=command,
                    failure_reason=str(exc),
                    return_code=return_code,
                    wall_clock_seconds=wall_clock,
                    executed_train_sha256=executed_train_sha256,
                    audit=audit,
                    started_at=evaluation_started_at,
                )
            if not math.isclose(
                float(metrics["val_bpb"]),
                attested_val_bpb,
                rel_tol=0.0,
                abs_tol=5.1e-7,
            ):
                return self._write_failure(
                    output_dir=output_dir,
                    evaluation_id=evaluation_id,
                    revision_id=revision_id,
                    candidate_sha256=revision.train_sha256,
                    seed=seed,
                    status=EvaluationStatus.INVALID_METRICS,
                    command=command,
                    failure_reason="candidate val_bpb differs from host-attested evaluate_bpb result",
                    return_code=return_code,
                    wall_clock_seconds=wall_clock,
                    executed_train_sha256=executed_train_sha256,
                    audit=audit,
                    started_at=evaluation_started_at,
                )
            metrics["val_bpb"] = attested_val_bpb
        if self.enforce_wall_clock_budget and wall_clock < self.manifest.training_seconds_tolerance[0]:
            return self._write_failure(
                output_dir=output_dir,
                evaluation_id=evaluation_id,
                revision_id=revision_id,
                candidate_sha256=revision.train_sha256,
                seed=seed,
                status=EvaluationStatus.INVALID_METRICS,
                command=command,
                failure_reason="candidate process wall-clock is shorter than the frozen training budget",
                return_code=return_code,
                wall_clock_seconds=wall_clock,
                executed_train_sha256=executed_train_sha256,
                audit=audit,
                started_at=evaluation_started_at,
            )
        evaluation = CandidateEvaluation(
            evaluation_id=evaluation_id,
            revision_id=revision_id,
            candidate_sha256=revision.train_sha256,
            seed=seed,
            status=EvaluationStatus.COMPLETED,
            score_valid=True,
            val_bpb=float(metrics["val_bpb"]),
            metrics=metrics,
            return_code=return_code,
            timed_out=False,
            wall_clock_seconds=wall_clock,
            command=command,
            executed_train_sha256=executed_train_sha256,
            stdout_sha256=stdout_sha256,
            stderr_sha256=stderr_sha256,
            **audit,
            started_at=evaluation_started_at,
            finished_at=datetime.now(timezone.utc).isoformat(),
        )
        evaluation.validate()
        write_json_exclusive(
            output_dir / "evaluation.json",
            {**evaluation.to_dict(), "evaluation_digest": evaluation.digest},
        )
        return evaluation

    def _write_failure(
        self,
        *,
        output_dir: Path,
        evaluation_id: str,
        revision_id: str,
        candidate_sha256: str,
        seed: int,
        status: EvaluationStatus,
        command: tuple[str, ...],
        failure_reason: str,
        return_code: int | None = None,
        timed_out: bool = False,
        wall_clock_seconds: float = 0.0,
        executed_train_sha256: str | None = None,
        audit: Mapping[str, object] | None = None,
        started_at: str | None = None,
    ) -> CandidateEvaluation:
        stdout_path = output_dir / "stdout.log"
        stderr_path = output_dir / "stderr.log"
        evaluation = CandidateEvaluation(
            evaluation_id=evaluation_id,
            revision_id=revision_id,
            candidate_sha256=candidate_sha256,
            seed=seed,
            status=status,
            score_valid=False,
            val_bpb=None,
            metrics={},
            return_code=return_code,
            timed_out=timed_out,
            wall_clock_seconds=wall_clock_seconds,
            command=command,
            executed_train_sha256=executed_train_sha256,
            stdout_sha256=sha256_file(stdout_path) if stdout_path.is_file() else None,
            stderr_sha256=sha256_file(stderr_path) if stderr_path.is_file() else None,
            failure_reason=failure_reason,
            **dict(audit or {}),
            started_at=started_at,
            finished_at=datetime.now(timezone.utc).isoformat(),
        )
        evaluation.validate()
        write_json_exclusive(
            output_dir / "evaluation.json",
            {**evaluation.to_dict(), "evaluation_digest": evaluation.digest},
        )
        return evaluation

    @staticmethod
    def _write_text_exclusive(path: Path, content: str) -> None:
        try:
            with path.open("x", encoding="utf-8", errors="strict") as handle:
                handle.write(content)
        except FileExistsError as exc:
            raise AdapterError(f"refusing to overwrite Autoresearch evaluator log: {path}") from exc

    @staticmethod
    def _write_host_runner(path: Path) -> None:
        source = """from __future__ import annotations

import runpy
import os
import sys
from pathlib import Path

train_path = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(train_path.parent))
import prepare

observed = []
original_evaluate_bpb = prepare.evaluate_bpb
host_write = os.write
protected_globals = {
    name: original_evaluate_bpb.__globals__[name]
    for name in ("EVAL_TOKENS", "MAX_SEQ_LEN", "Tokenizer", "make_dataloader")
    if name in original_evaluate_bpb.__globals__
}

def host_tracked_evaluate_bpb(*args, **kwargs):
    original_evaluate_bpb.__globals__.update(protected_globals)
    value = original_evaluate_bpb(*args, **kwargs)
    observed.append(float(value))
    return value

prepare.evaluate_bpb = host_tracked_evaluate_bpb
candidate_error = None
try:
    runpy.run_path(str(train_path), run_name="__main__")
except BaseException as exc:
    candidate_error = exc
if len(observed) != 1:
    raise RuntimeError(f"expected one evaluate_bpb call, observed {len(observed)}")
host_write(1, f"AUTORESEARCH_HOST_VAL_BPB:{observed[0]:.17g}\\n".encode("ascii"))
if candidate_error is not None:
    raise candidate_error
"""
        try:
            with path.open("x", encoding="utf-8") as handle:
                handle.write(source)
        except FileExistsError as exc:
            raise AdapterError(f"refusing to overwrite Autoresearch host runner: {path}") from exc

    @staticmethod
    def _validate_protected_assets(
        store: TrainRevisionStore,
        workspace: Path,
        *,
        executed_train_sha256: str,
    ) -> None:
        train_path = workspace / "train.py"
        if (
            not train_path.is_file()
            or train_path.is_symlink()
            or sha256_file(train_path) != executed_train_sha256
        ):
            raise AdapterError("Autoresearch evaluator candidate modified train.py during execution")
        for relative in store.baseline_manifest.protected_paths:
            path = workspace / relative
            expected = store.baseline_manifest.source_files[relative]
            if not path.is_file() or path.is_symlink() or sha256_file(path) != expected:
                raise AdapterError(f"Autoresearch evaluator protected asset drift: {relative}")

    def _sandbox_command(
        self,
        *,
        training_command: tuple[str, ...],
        workspace: Path,
        home: Path,
        host_runner: Path | None,
    ) -> tuple[str, ...]:
        if not self.sandbox:
            return training_command
        bubblewrap = shutil.which("bwrap")
        if bubblewrap is None:
            raise AdapterError("Autoresearch formal evaluator requires bubblewrap isolation")
        executable = Path(training_command[0])
        if not executable.is_absolute():
            resolved_name = shutil.which(training_command[0])
            if resolved_name is None:
                raise AdapterError(
                    f"Autoresearch evaluator executable is unavailable: {training_command[0]}"
                )
            executable = Path(resolved_name)
        original_executable = Path(training_command[0]).expanduser()
        if not original_executable.is_absolute():
            original_executable = Path(shutil.which(training_command[0]) or training_command[0])
        executable = executable.resolve()
        runtime_paths = [original_executable.parent.parent, executable.parents[1]]
        argv: list[str] = [
            bubblewrap,
            "--die-with-parent",
            "--new-session",
            "--unshare-net",
            "--ro-bind",
            "/usr",
            "/usr",
            "--ro-bind",
            "/bin",
            "/bin",
            "--ro-bind",
            "/lib",
            "/lib",
            "--ro-bind",
            "/lib64",
            "/lib64",
            "--ro-bind",
            "/etc/ssl",
            "/etc/ssl",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--ro-bind",
            "/sys",
            "/sys",
            "--tmpfs",
            "/tmp",
        ]
        if self.gpu_id is not None:
            device_paths = [
                Path(f"/dev/nvidia{self.gpu_id}"),
                Path("/dev/nvidiactl"),
                Path("/dev/nvidia-uvm"),
                Path("/dev/nvidia-uvm-tools"),
                Path("/dev/nvidia-modeset"),
            ]
            selected = device_paths[0]
            if not selected.exists():
                raise AdapterError(f"Autoresearch selected GPU device is missing: {selected}")
            for device in device_paths:
                if device.exists():
                    argv.extend(("--dev-bind", str(device), str(device)))
        created_mounts = {
            Path("/tmp"),
            Path("/usr"),
            Path("/bin"),
            Path("/lib"),
            Path("/lib64"),
        }
        for target in (workspace, home):
            current = Path("/")
            for part in target.parent.parts[1:]:
                current /= part
                if current not in created_mounts:
                    argv.extend(("--dir", str(current)))
                    created_mounts.add(current)
        argv.extend(
            (
                "--ro-bind",
                str(workspace),
                str(workspace),
                "--bind",
                str(home),
                str(home),
                "--ro-bind",
                str(self.prepared_root),
                str(home / ".cache/autoresearch"),
            )
        )
        if self.kernel_cache_root is not None:
            argv.extend(
                (
                    "--ro-bind",
                    str(self.kernel_cache_root),
                    str(home / ".cache/huggingface/hub/models--varunneal--flash-attention-3"),
                )
            )
        if host_runner is not None:
            argv.extend(("--ro-bind", str(host_runner), str(host_runner)))
        for runtime_path in dict.fromkeys(runtime_paths):
            if runtime_path in created_mounts or runtime_path in {Path("/usr"), Path("/")}:
                continue
            current = Path("/")
            for part in runtime_path.parent.parts[1:]:
                current /= part
                if current not in created_mounts:
                    argv.extend(("--dir", str(current)))
                    created_mounts.add(current)
            argv.extend(("--ro-bind", str(runtime_path), str(runtime_path)))
            created_mounts.add(runtime_path)
        argv.extend(("--chdir", str(workspace), "--", *training_command))
        return tuple(argv)


__all__ = [
    "CandidateEvaluation",
    "CandidateEvaluator",
    "EvaluationStatus",
    "EvaluatorManifest",
    "parse_attested_val_bpb",
    "parse_metrics",
]
