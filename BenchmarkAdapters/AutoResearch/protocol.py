"""Frozen Autoresearch Architecture Design reconstruction protocol."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ..contracts import AdapterError
from ..protocol import canonical_json, sha256_file, write_json_exclusive
from ..registry import ROOT
from ..task_specs import task_spec_digest
from .baseline import BaselineManifest, KernelCacheManifest, PreparedAssetManifest
from .evaluator import EvaluatorManifest
from .seed_injection import SeedPolicy


DEFAULT_ASSET_DIR = ROOT / "autoresearch/protocol"


def _portable(path: Path) -> str:
    resolved = path.resolve()
    try:
        return "repo:" + resolved.relative_to(ROOT).as_posix()
    except ValueError:
        return str(resolved)


def _resolve(value: str, protocol_path: Path) -> Path:
    if value.startswith("repo:"):
        return (ROOT / value.removeprefix("repo:")).resolve()
    if value.startswith("asset:"):
        return (protocol_path.parent / value.removeprefix("asset:")).resolve()
    return Path(value).expanduser().resolve()


@dataclass(frozen=True)
class BaselineScoreRecord:
    schema_version: int
    protocol_id: str
    status: str
    baseline_train_sha256: str
    baseline_manifest_digest: str
    prepared_manifest_digest: str
    kernel_cache_manifest_digest: str
    evaluator_manifest_digest: str
    seed_policy_digest: str
    primary_metric: str
    metric_direction: str
    held_out_seeds: tuple[int, ...]
    held_out_scores: dict[str, float]
    final_score: float | None
    evaluation_record_digests: dict[str, str]
    note: str
    benchmark_commit: str | None = None
    hardware_fingerprint: str | None = None
    evidence_files: dict[str, str] | None = None

    @classmethod
    def load(cls, path: Path) -> "BaselineScoreRecord":
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            record = cls(
                schema_version=int(payload["schema_version"]),
                protocol_id=str(payload["protocol_id"]),
                status=str(payload["status"]),
                baseline_train_sha256=str(payload["baseline_train_sha256"]),
                baseline_manifest_digest=str(payload["baseline_manifest_digest"]),
                prepared_manifest_digest=str(payload["prepared_manifest_digest"]),
                kernel_cache_manifest_digest=str(payload["kernel_cache_manifest_digest"]),
                evaluator_manifest_digest=str(payload["evaluator_manifest_digest"]),
                seed_policy_digest=str(payload["seed_policy_digest"]),
                primary_metric=str(payload["primary_metric"]),
                metric_direction=str(payload["metric_direction"]),
                held_out_seeds=tuple(int(seed) for seed in payload["held_out_seeds"]),
                held_out_scores={
                    str(seed): float(score) for seed, score in payload["held_out_scores"].items()
                },
                final_score=(
                    None if payload["final_score"] is None else float(payload["final_score"])
                ),
                evaluation_record_digests={
                    str(seed): str(digest)
                    for seed, digest in payload["evaluation_record_digests"].items()
                },
                note=str(payload["note"]),
                benchmark_commit=(
                    None
                    if payload.get("benchmark_commit") is None
                    else str(payload["benchmark_commit"])
                ),
                hardware_fingerprint=(
                    None
                    if payload.get("hardware_fingerprint") is None
                    else str(payload["hardware_fingerprint"])
                ),
                evidence_files=(
                    None
                    if payload.get("evidence_files") is None
                    else {
                        str(relative): str(digest)
                        for relative, digest in payload["evidence_files"].items()
                    }
                ),
            )
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise AdapterError(f"invalid Autoresearch baseline score record: {path}") from exc
        record.validate(path.parent)
        expected_digest = payload.get("record_digest")
        if expected_digest is not None and expected_digest != record.digest:
            raise AdapterError(f"Autoresearch baseline score digest mismatch: {path}")
        return record

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["held_out_seeds"] = list(self.held_out_seeds)
        if self.schema_version == 1:
            payload.pop("benchmark_commit", None)
            payload.pop("hardware_fingerprint", None)
            payload.pop("evidence_files", None)
        return payload

    @property
    def digest(self) -> str:
        self.validate()
        return hashlib.sha256(canonical_json(self.to_dict())).hexdigest()

    def validate(self, evidence_root: Path | None = None) -> None:
        if self.schema_version not in {1, 2}:
            raise AdapterError("unsupported Autoresearch baseline score schema")
        if self.protocol_id != "autoresearch-architecture-reconstruction-v1":
            raise AdapterError("Autoresearch baseline score record uses a different protocol ID")
        if self.status not in {"pending", "completed"}:
            raise AdapterError("Autoresearch baseline score status must be pending or completed")
        digests = (
            self.baseline_train_sha256,
            self.baseline_manifest_digest,
            self.prepared_manifest_digest,
            self.kernel_cache_manifest_digest,
            self.evaluator_manifest_digest,
            self.seed_policy_digest,
        )
        if any(
            len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
            for value in digests
        ):
            raise AdapterError("Autoresearch baseline score record contains an invalid digest")
        if self.primary_metric != "val_bpb" or self.metric_direction != "minimize":
            raise AdapterError("Autoresearch baseline score record must minimize val_bpb")
        if len(self.held_out_seeds) != 2 or len(set(self.held_out_seeds)) != 2:
            raise AdapterError("Autoresearch baseline score record requires two held-out seeds")
        if not self.note.strip():
            raise AdapterError("Autoresearch baseline score record requires an explanatory note")
        if self.status == "pending":
            if (
                self.held_out_scores
                or self.final_score is not None
                or self.evaluation_record_digests
                or self.benchmark_commit is not None
                or self.hardware_fingerprint is not None
                or self.evidence_files
            ):
                raise AdapterError("pending Autoresearch baseline score record cannot contain scores")
            return
        if self.schema_version != 2:
            raise AdapterError("completed Autoresearch baseline score records require schema_version 2")
        expected_keys = {str(seed) for seed in self.held_out_seeds}
        if set(self.held_out_scores) != expected_keys:
            raise AdapterError("completed Autoresearch baseline score record is missing held-out scores")
        if set(self.evaluation_record_digests) != expected_keys:
            raise AdapterError("completed Autoresearch baseline score record is missing evaluation digests")
        if any(
            len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest)
            for digest in self.evaluation_record_digests.values()
        ):
            raise AdapterError("Autoresearch baseline evaluation digest is invalid")
        values = tuple(self.held_out_scores[str(seed)] for seed in self.held_out_seeds)
        if any(not math.isfinite(value) or value <= 0 for value in values):
            raise AdapterError("Autoresearch baseline held-out scores must be finite and positive")
        expected_score = sum(values) / len(values)
        if self.final_score is None or not math.isclose(
            self.final_score, expected_score, rel_tol=0.0, abs_tol=1e-12
        ):
            raise AdapterError("Autoresearch baseline final score differs from held-out mean")
        if self.benchmark_commit is None or len(self.benchmark_commit) != 40:
            raise AdapterError("Autoresearch baseline benchmark commit is invalid")
        if self.hardware_fingerprint is None or len(self.hardware_fingerprint) != 64:
            raise AdapterError("Autoresearch baseline hardware fingerprint is invalid")
        if not self.evidence_files:
            raise AdapterError("Autoresearch completed baseline requires hashed evidence")
        required_evidence = {"baseline-evidence/hardware.json"}
        for index in range(1, 3):
            required_evidence.update(
                {
                    f"baseline-evidence/held-out-{index}/evaluation.json",
                    f"baseline-evidence/held-out-{index}/stdout.log",
                    f"baseline-evidence/held-out-{index}/stderr.log",
                    f"baseline-evidence/held-out-{index}/workspace/train.py",
                }
            )
        if not required_evidence.issubset(self.evidence_files):
            raise AdapterError("Autoresearch baseline evidence manifest is incomplete")
        if evidence_root is None:
            return
        evidence_root = evidence_root.resolve()
        for relative, digest in self.evidence_files.items():
            path = evidence_root / relative
            if (
                len(digest) != 64
                or not path.is_file()
                or path.is_symlink()
                or sha256_file(path) != digest
            ):
                raise AdapterError(f"Autoresearch baseline evidence drift: {relative}")
        hardware = json.loads(
            (evidence_root / "baseline-evidence/hardware.json").read_text(encoding="utf-8")
        )
        if hashlib.sha256(canonical_json(hardware)).hexdigest() != self.hardware_fingerprint:
            raise AdapterError("Autoresearch baseline hardware evidence fingerprint differs")
        for index, seed in enumerate(self.held_out_seeds, 1):
            evaluation_path = (
                evidence_root / f"baseline-evidence/held-out-{index}/evaluation.json"
            )
            evaluation = json.loads(evaluation_path.read_text(encoding="utf-8"))
            evaluation_digest = evaluation.pop("evaluation_digest", None)
            if evaluation_digest != hashlib.sha256(canonical_json(evaluation)).hexdigest():
                raise AdapterError("Autoresearch baseline evaluation digest mismatch")
            if not (
                evaluation.get("status") == "completed"
                and evaluation.get("score_valid") is True
                and evaluation.get("seed") == seed
                and evaluation.get("candidate_sha256") == self.baseline_train_sha256
                and evaluation.get("benchmark_commit") == self.benchmark_commit
                and evaluation.get("gpu_uuid")
                and evaluation.get("evaluator_digest")
                and evaluation.get("environment_digest")
                and evaluation.get("evaluator_version") == "autoresearch-evaluator-v2"
                and evaluation.get("val_bpb") == self.held_out_scores[str(seed)]
                and evaluation_digest == self.evaluation_record_digests[str(seed)]
            ):
                raise AdapterError("Autoresearch baseline evaluation evidence is inconsistent")


@dataclass(frozen=True)
class AutoResearchProtocol:
    protocol_id: str
    source_root: Path
    baseline_manifest_path: Path
    baseline_manifest_digest: str
    prepared_manifest_path: Path
    prepared_manifest_digest: str
    kernel_cache_manifest_path: Path
    kernel_cache_manifest_digest: str
    evaluator_manifest_path: Path
    evaluator_manifest_digest: str
    seed_policy_path: Path
    seed_policy_digest: str
    environment_lock_path: Path
    environment_lock_digest: str
    baseline_score_record_path: Path
    baseline_score_record_digest: str
    candidate_training_seconds: int
    candidate_timeout_seconds: int
    outer_wall_clock_seconds: int
    outer_seeds: tuple[int, ...]
    outer_model: str
    reasoning_effort: str
    temperature: float | None
    model_provider_policy: str
    gpu_policy: str
    cpu_policy: str
    memory_policy: str
    editable_paths: tuple[str, ...]
    failure_policy: str
    artifact_policy: str
    aggregation_policy: str
    schema_version: int = 1

    def validate(
        self,
        prepared_root: Path | None = None,
        kernel_cache_root: Path | None = None,
    ) -> None:
        if self.schema_version not in {1, 2}:
            raise AdapterError("unsupported Autoresearch protocol schema")
        if self.protocol_id != "autoresearch-architecture-reconstruction-v1":
            raise AdapterError("unexpected Autoresearch protocol ID")
        baseline = BaselineManifest.load(self.baseline_manifest_path)
        prepared = PreparedAssetManifest.load(self.prepared_manifest_path)
        kernel_cache = KernelCacheManifest.load(self.kernel_cache_manifest_path)
        seed_policy = SeedPolicy.load(self.seed_policy_path)
        baseline_score = BaselineScoreRecord.load(self.baseline_score_record_path)
        if baseline.digest != self.baseline_manifest_digest:
            raise AdapterError("Autoresearch baseline manifest differs from protocol")
        if prepared.digest != self.prepared_manifest_digest:
            raise AdapterError("Autoresearch prepared manifest differs from protocol")
        if kernel_cache.digest != self.kernel_cache_manifest_digest:
            raise AdapterError("Autoresearch kernel cache manifest differs from protocol")
        if seed_policy.digest != self.seed_policy_digest:
            raise AdapterError("Autoresearch seed policy differs from protocol")
        if (
            seed_policy.protocol_id != self.protocol_id
            or prepared.protocol_id != self.protocol_id
            or kernel_cache.protocol_id != self.protocol_id
        ):
            raise AdapterError("Autoresearch protocol assets use a different protocol ID")
        if not self.evaluator_manifest_path.is_file():
            raise AdapterError("Autoresearch evaluator manifest is missing")
        if sha256_file(self.evaluator_manifest_path) != self.evaluator_manifest_digest:
            raise AdapterError("Autoresearch evaluator manifest differs from protocol")
        EvaluatorManifest.load(self.evaluator_manifest_path)
        if not self.environment_lock_path.is_file():
            raise AdapterError("Autoresearch UV lock is missing")
        if sha256_file(self.environment_lock_path) != self.environment_lock_digest:
            raise AdapterError("Autoresearch environment lock differs from protocol")
        if sha256_file(self.baseline_score_record_path) != self.baseline_score_record_digest:
            raise AdapterError("Autoresearch baseline score record differs from protocol")
        if (
            baseline_score.baseline_train_sha256 != baseline.source_files["train.py"]
            or baseline_score.baseline_manifest_digest != self.baseline_manifest_digest
            or baseline_score.prepared_manifest_digest != self.prepared_manifest_digest
            or baseline_score.kernel_cache_manifest_digest != self.kernel_cache_manifest_digest
            or baseline_score.evaluator_manifest_digest != self.evaluator_manifest_digest
            or baseline_score.seed_policy_digest != self.seed_policy_digest
            or baseline_score.held_out_seeds != seed_policy.held_out_seeds
            or (
                baseline_score.status == "completed"
                and baseline_score.benchmark_commit != baseline.source_commit
            )
        ):
            raise AdapterError("Autoresearch baseline score record differs from frozen assets")
        baseline.validate(self.source_root)
        if prepared_root is not None:
            prepared.validate(prepared_root)
        if kernel_cache_root is not None:
            kernel_cache.validate(kernel_cache_root)
        if self.candidate_training_seconds != 300:
            raise AdapterError("Autoresearch candidate training budget must be 300 seconds")
        if self.candidate_timeout_seconds < 300 or self.candidate_timeout_seconds > 900:
            raise AdapterError("Autoresearch candidate hard timeout must be between 300 and 900 seconds")
        if self.outer_wall_clock_seconds != 172800:
            raise AdapterError("Autoresearch formal outer budget must be 48 hours")
        if len(self.outer_seeds) not in {1, 3} or len(set(self.outer_seeds)) != len(
            self.outer_seeds
        ):
            raise AdapterError(
                "Autoresearch outer repetitions must contain one or three unique run IDs"
            )
        if not self.outer_model.strip() or not self.reasoning_effort.strip():
            raise AdapterError("Autoresearch legacy model fields must not be empty")
        if self.editable_paths != ("train.py",) or baseline.editable_paths != self.editable_paths:
            raise AdapterError("Autoresearch editable allowlist must be train.py only")
        policies = (
            self.gpu_policy,
            self.cpu_policy,
            self.memory_policy,
            self.model_provider_policy,
            self.failure_policy,
            self.artifact_policy,
            self.aggregation_policy,
        )
        if any(not value.strip() for value in policies):
            raise AdapterError("Autoresearch protocol policies must not be empty")
        if "H100" not in self.gpu_policy:
            raise AdapterError("Autoresearch formal protocol requires an H100 policy")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if self.schema_version == 1:
            payload.pop("schema_version")
        for field in (
            "source_root",
            "baseline_manifest_path",
            "prepared_manifest_path",
            "kernel_cache_manifest_path",
            "evaluator_manifest_path",
            "seed_policy_path",
            "environment_lock_path",
            "baseline_score_record_path",
        ):
            payload[field] = _portable(getattr(self, field))
        payload["outer_seeds"] = list(self.outer_seeds)
        payload["editable_paths"] = list(self.editable_paths)
        return payload

    @property
    def outer_repetitions(self) -> int:
        return len(self.outer_seeds)

    def protocol_asset_digests(self) -> dict[str, str]:
        return {
            "task_spec": task_spec_digest("autoresearch-architecture"),
            "baseline_manifest": self.baseline_manifest_digest,
            "prepared_manifest": self.prepared_manifest_digest,
            "kernel_cache_manifest": self.kernel_cache_manifest_digest,
            "evaluator_manifest": self.evaluator_manifest_digest,
            "seed_policy": self.seed_policy_digest,
            "environment_lock": self.environment_lock_digest,
            "baseline_score_record": self.baseline_score_record_digest,
        }

    @property
    def digest(self) -> str:
        self.validate()
        return hashlib.sha256(canonical_json(self.to_dict())).hexdigest()

    def write(self, path: Path) -> None:
        write_json_exclusive(path, {**self.to_dict(), "protocol_digest": self.digest})

    @classmethod
    def load(
        cls,
        path: Path,
        prepared_root: Path | None = None,
        kernel_cache_root: Path | None = None,
    ) -> "AutoResearchProtocol":
        payload = json.loads(path.read_text(encoding="utf-8"))
        expected = payload.pop("protocol_digest", None)
        for field in (
            "source_root",
            "baseline_manifest_path",
            "prepared_manifest_path",
            "kernel_cache_manifest_path",
            "evaluator_manifest_path",
            "seed_policy_path",
            "environment_lock_path",
            "baseline_score_record_path",
        ):
            payload[field] = _resolve(str(payload[field]), path)
        payload["outer_seeds"] = tuple(int(seed) for seed in payload["outer_seeds"])
        payload["editable_paths"] = tuple(payload["editable_paths"])
        protocol = cls(**payload)
        protocol.validate(prepared_root, kernel_cache_root)
        if expected != protocol.digest:
            raise AdapterError(f"Autoresearch protocol digest mismatch: {path}")
        return protocol

    @property
    def formal_baseline_ready(self) -> bool:
        return BaselineScoreRecord.load(self.baseline_score_record_path).status == "completed"

    def require_formal_contract(self) -> None:
        if (
            self.schema_version != 2
            or self.outer_model != "configured-by-model-track"
            or self.reasoning_effort != "configured-by-model-track"
            or self.temperature is not None
        ):
            raise AdapterError(
                "formal Autoresearch requires a reviewed schema-v2 model-track protocol candidate"
            )

    def require_formal_baseline(self) -> BaselineScoreRecord:
        self.require_formal_contract()
        record = BaselineScoreRecord.load(self.baseline_score_record_path)
        if record.status != "completed":
            raise AdapterError(
                "formal Autoresearch campaign requires a completed frozen baseline score record"
            )
        record.validate(self.baseline_score_record_path.parent)
        return record


def build_protocol(
    asset_dir: Path = DEFAULT_ASSET_DIR,
    *,
    outer_repetitions: int = 1,
) -> AutoResearchProtocol:
    if outer_repetitions not in {1, 3}:
        raise AdapterError("Autoresearch outer_repetitions must be 1 or 3")
    asset_dir = asset_dir.resolve()
    baseline_path = asset_dir / "baseline_manifest.json"
    prepared_path = asset_dir / "prepared_manifest.json"
    kernel_cache_path = asset_dir / "kernel_cache_manifest.json"
    evaluator_path = asset_dir / "evaluator_manifest.json"
    seed_path = asset_dir / "seed_policy.json"
    baseline_score_path = asset_dir / "baseline_score_record.json"
    protocol = AutoResearchProtocol(
        protocol_id="autoresearch-architecture-reconstruction-v1",
        source_root=ROOT / "autoresearch",
        baseline_manifest_path=baseline_path,
        baseline_manifest_digest=BaselineManifest.load(baseline_path).digest,
        prepared_manifest_path=prepared_path,
        prepared_manifest_digest=PreparedAssetManifest.load(prepared_path).digest,
        kernel_cache_manifest_path=kernel_cache_path,
        kernel_cache_manifest_digest=KernelCacheManifest.load(kernel_cache_path).digest,
        evaluator_manifest_path=evaluator_path,
        evaluator_manifest_digest=sha256_file(evaluator_path),
        seed_policy_path=seed_path,
        seed_policy_digest=SeedPolicy.load(seed_path).digest,
        environment_lock_path=ROOT / "autoresearch/uv.lock",
        environment_lock_digest=sha256_file(ROOT / "autoresearch/uv.lock"),
        baseline_score_record_path=baseline_score_path,
        baseline_score_record_digest=sha256_file(baseline_score_path),
        candidate_training_seconds=300,
        candidate_timeout_seconds=900,
        outer_wall_clock_seconds=172800,
        outer_seeds=tuple(range(outer_repetitions)),
        outer_model="configured-by-model-track",
        reasoning_effort="configured-by-model-track",
        temperature=None,
        model_provider_policy="one explicit model-track configuration shared by all seven Agents",
        gpu_policy="one exclusive NVIDIA H100 80GB per outer run and candidate evaluation",
        cpu_policy="fixed cpuset and CPU count recorded in each run manifest",
        memory_policy="fixed RAM limit recorded in each run manifest",
        editable_paths=("train.py",),
        failure_policy="failed-or-missing outer cells remain explicit and are not replaced",
        artifact_policy="one dev-selected train.py replayed from frozen baseline and hash-published",
        aggregation_policy="single_run for N=1 or Avg@3 for N=3 over two-seed held-out means",
        schema_version=2,
    )
    protocol.validate()
    return protocol


__all__ = [
    "AutoResearchProtocol",
    "BaselineScoreRecord",
    "DEFAULT_ASSET_DIR",
    "build_protocol",
]
