"""Frozen reconstruction protocol for modded-NanoGPT Track 3."""

from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from ..contracts import AdapterError
from ..protocol import canonical_json, sha256_file, write_json_exclusive
from ..registry import ROOT
from ..task_specs import task_spec_digest
from .runtime import AgentRuntimeManifest


DEFAULT_ASSET_DIR = ROOT / "optimizer-design/protocol"
DEFAULT_SOURCE_ROOT = Path(
    os.environ.get(
        "OPTIMIZER_DESIGN_SOURCE_ROOT",
        "/mnt/sda/shijianwang/benchmark-deployments/repos/modded-nanogpt",
    )
)
DEFAULT_DATA_ROOT = Path(
    os.environ.get(
        "OPTIMIZER_DESIGN_DATA_ROOT",
        "/mnt/sda/shijianwang/benchmark-deployments/data/modded-nanogpt",
    )
)
DEFAULT_ENVIRONMENT_PYTHON = Path(
    os.environ.get(
        "OPTIMIZER_DESIGN_ENVIRONMENT_PYTHON",
        "/mnt/sda/shijianwang/benchmark-deployments/envs/modded-nanogpt/bin/python",
    )
)


def _portable_path(path: Path, asset_dir: Path) -> str:
    path = path.resolve()
    try:
        return f"repo:{path.relative_to(ROOT.resolve()).as_posix()}"
    except ValueError:
        return str(path)


def _resolve_portable_path(value: str, asset_dir: Path) -> Path:
    if value.startswith("asset:"):
        relative = value.removeprefix("asset:")
        _validate_relative(relative)
        return (asset_dir / relative).resolve()
    if value.startswith("repo:"):
        relative = value.removeprefix("repo:")
        _validate_relative(relative)
        return (ROOT / relative).resolve()
    return Path(value).expanduser().resolve()


def _load_json(path: Path, description: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AdapterError(f"invalid Optimizer Design {description}: {path}") from exc
    if not isinstance(payload, dict):
        raise AdapterError(f"Optimizer Design {description} must be a JSON object: {path}")
    return payload


def _valid_digest(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _validate_relative(relative: str) -> None:
    path = PurePosixPath(relative)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise AdapterError(f"unsafe Optimizer Design asset path: {relative}")


@dataclass(frozen=True)
class SourceManifest:
    repository: str
    source_commit: str
    track_tree: str
    files: Mapping[str, str]
    editable_path: str
    baseline_candidate_sha256: str
    identity: str

    @property
    def digest(self) -> str:
        return hashlib.sha256(canonical_json(self.to_dict())).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "repository": self.repository,
            "source_commit": self.source_commit,
            "track_tree": self.track_tree,
            "files": dict(sorted(self.files.items())),
            "editable_path": self.editable_path,
            "baseline_candidate_sha256": self.baseline_candidate_sha256,
            "identity": self.identity,
        }

    def validate(self, source_root: Path | None = None) -> None:
        if self.repository != "https://github.com/KellerJordan/modded-nanogpt.git":
            raise AdapterError("unexpected Optimizer Design upstream repository")
        if len(self.source_commit) != 40 or len(self.track_tree) != 40:
            raise AdapterError("invalid Optimizer Design source identity")
        if self.identity != "current-official-track-3-reconstruction":
            raise AdapterError("Optimizer Design protocol must identify the reconstruction track")
        if self.editable_path != "records/track_3_optimization/train_gpt_simple.py":
            raise AdapterError("Optimizer Design editable path must be the Track 3 train script")
        if not self.files or self.editable_path not in self.files:
            raise AdapterError("Optimizer Design source manifest is incomplete")
        if self.baseline_candidate_sha256 != self.files[self.editable_path]:
            raise AdapterError("Optimizer Design baseline candidate digest is inconsistent")
        for relative, digest in self.files.items():
            _validate_relative(relative)
            if not _valid_digest(digest):
                raise AdapterError(f"invalid Optimizer Design source digest: {relative}")
        if source_root is None:
            return
        source_root = source_root.resolve()
        if not (source_root / ".git").is_dir():
            raise AdapterError(f"Optimizer Design source checkout is missing: {source_root}")
        head = subprocess.run(
            ["git", "-C", str(source_root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
        tree = subprocess.run(
            [
                "git",
                "-C",
                str(source_root),
                "rev-parse",
                f"{self.source_commit}:records/track_3_optimization",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if head.returncode or head.stdout.strip() != self.source_commit:
            raise AdapterError("Optimizer Design checkout is not at the frozen commit")
        if tree.returncode or tree.stdout.strip() != self.track_tree:
            raise AdapterError("Optimizer Design Track 3 tree differs from the frozen tree")
        for relative, expected in self.files.items():
            path = source_root / relative
            if not path.is_file() or path.is_symlink() or sha256_file(path) != expected:
                raise AdapterError(f"Optimizer Design frozen source drift: {relative}")

    @classmethod
    def load(cls, path: Path) -> "SourceManifest":
        payload = _load_json(path, "source manifest")
        expected = payload.pop("manifest_digest", None)
        manifest = cls(
            repository=str(payload["repository"]),
            source_commit=str(payload["source_commit"]),
            track_tree=str(payload["track_tree"]),
            files=dict(payload["files"]),
            editable_path=str(payload["editable_path"]),
            baseline_candidate_sha256=str(payload["baseline_candidate_sha256"]),
            identity=str(payload["identity"]),
        )
        manifest.validate()
        if expected != manifest.digest:
            raise AdapterError(f"Optimizer Design source manifest digest mismatch: {path}")
        return manifest


@dataclass(frozen=True)
class DataManifest:
    dataset: str
    scope: str
    files: Mapping[str, str]

    @property
    def digest(self) -> str:
        return hashlib.sha256(canonical_json(self.to_dict())).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset": self.dataset,
            "scope": self.scope,
            "files": dict(sorted(self.files.items())),
        }

    def validate(self, data_root: Path | None = None) -> None:
        if self.dataset != "kjj0/fineweb10B-gpt2":
            raise AdapterError("unexpected Optimizer Design dataset")
        training = [name for name in self.files if "fineweb_train_" in name]
        validation = [name for name in self.files if "fineweb_val_" in name]
        if len(training) != 20 or len(validation) != 1:
            raise AdapterError("Optimizer Design requires 20 training shards and one validation shard")
        for relative, digest in self.files.items():
            _validate_relative(relative)
            if not _valid_digest(digest):
                raise AdapterError(f"invalid Optimizer Design data digest: {relative}")
        if data_root is None:
            return
        data_root = data_root.resolve()
        actual = {
            path.relative_to(data_root).as_posix()
            for path in data_root.rglob("*.bin")
            if path.is_file() and not path.is_symlink()
        }
        if actual != set(self.files):
            raise AdapterError("Optimizer Design data tree differs from the frozen manifest")
        for relative, expected in self.files.items():
            path = data_root / relative
            if not path.is_file() or path.is_symlink() or sha256_file(path) != expected:
                raise AdapterError(f"Optimizer Design data drift: {relative}")

    @classmethod
    def load(cls, path: Path) -> "DataManifest":
        payload = _load_json(path, "data manifest")
        expected = payload.pop("manifest_digest", None)
        manifest = cls(
            dataset=str(payload["dataset"]),
            scope=str(payload["scope"]),
            files=dict(payload["files"]),
        )
        manifest.validate()
        if expected != manifest.digest:
            raise AdapterError(f"Optimizer Design data manifest digest mismatch: {path}")
        return manifest


@dataclass(frozen=True)
class EnvironmentManifest:
    python: str
    torch: str
    cuda_runtime: str
    triton: str
    huggingface_hub: str
    numpy: str
    python_executable_sha256: str
    package_fingerprint: str
    environment_sha256: str
    status: str

    @property
    def digest(self) -> str:
        return hashlib.sha256(canonical_json(asdict(self))).hexdigest()

    def validate(self, python: Path | None = None) -> None:
        if (
            self.python != "3.10.20"
            or self.torch != "2.11.0+cu128"
            or self.cuda_runtime != "12.8"
            or self.triton != "3.6.0"
            or self.huggingface_hub != "1.26.0"
            or self.numpy != "2.2.6"
        ):
            raise AdapterError("Optimizer Design environment differs from the frozen runtime")
        if self.status != "verified-h100-import-bf16-smoke-and-offline-lock-sync":
            raise AdapterError("Optimizer Design environment readiness status is invalid")
        for digest in (
            self.python_executable_sha256,
            self.package_fingerprint,
            self.environment_sha256,
        ):
            if not _valid_digest(digest):
                raise AdapterError("Optimizer Design environment content digest is invalid")
        if python is None:
            return
        python = python.expanduser().absolute()
        if not python.is_file() or not os.access(python, os.X_OK):
            raise AdapterError(f"Optimizer Design Python is unavailable: {python}")
        probe = subprocess.run(
            [
                str(python),
                "-c",
                (
                    "import json, platform, importlib.metadata as m, torch; "
                    "print(json.dumps({'python': platform.python_version(), "
                    "'torch': torch.__version__, 'cuda_runtime': torch.version.cuda, "
                    "'triton': m.version('triton'), 'huggingface_hub': "
                    "m.version('huggingface-hub'), 'numpy': m.version('numpy')}))"
                ),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if probe.returncode:
            raise AdapterError("Optimizer Design environment import probe failed")
        observed = json.loads(probe.stdout)
        expected = {
            "python": self.python,
            "torch": self.torch,
            "cuda_runtime": self.cuda_runtime,
            "triton": self.triton,
            "huggingface_hub": self.huggingface_hub,
            "numpy": self.numpy,
        }
        if observed != expected:
            raise AdapterError("Optimizer Design environment versions differ from the manifest")
        from .runtime import directory_digest, python_package_fingerprint

        if (
            sha256_file(python.resolve()) != self.python_executable_sha256
            or python_package_fingerprint(python) != self.package_fingerprint
            or directory_digest(python.parent.parent) != self.environment_sha256
        ):
            raise AdapterError("Optimizer Design environment content differs from the manifest")

    @classmethod
    def load(cls, path: Path) -> "EnvironmentManifest":
        payload = _load_json(path, "environment manifest")
        expected = payload.pop("manifest_digest", None)
        manifest = cls(**{name: str(value) for name, value in payload.items()})
        manifest.validate()
        if expected != manifest.digest:
            raise AdapterError(f"Optimizer Design environment manifest digest mismatch: {path}")
        return manifest


@dataclass(frozen=True)
class EvaluatorManifest:
    schema_version: int
    protocol_id: str
    implementation_files: Mapping[str, str]
    isolation_policy: str
    candidate_policy: str
    scoring_policy: str

    @property
    def digest(self) -> str:
        return hashlib.sha256(canonical_json(self.to_dict())).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["implementation_files"] = dict(sorted(self.implementation_files.items()))
        return payload

    def validate(self) -> None:
        if self.schema_version != 1:
            raise AdapterError("unsupported Optimizer Design evaluator manifest schema")
        if self.protocol_id != "modded-nanogpt-optimizer-design-reconstruction-v1":
            raise AdapterError("Optimizer Design evaluator manifest uses a different protocol ID")
        required = {
            "BenchmarkAdapters/AutoResearch/dev_client.py",
            "BenchmarkAdapters/AutoResearch/broker.py",
            "BenchmarkAdapters/AutoResearch/launchers/__init__.py",
            "BenchmarkAdapters/AutoResearch/launchers/ai_scientist.py",
            "BenchmarkAdapters/AutoResearch/launchers/arbor.py",
            "BenchmarkAdapters/AutoResearch/launchers/common.py",
            "BenchmarkAdapters/AutoResearch/launchers/ear.py",
            "BenchmarkAdapters/AutoResearch/launchers/ml_master_2.py",
            "BenchmarkAdapters/AutoResearch/launchers/mlevolve.py",
            "BenchmarkAdapters/AutoResearch/launchers/proposal.py",
            "BenchmarkAdapters/AutoResearch/launchers/runner.py",
            "BenchmarkAdapters/AutoResearch/launchers/sandbox.py",
            "BenchmarkAdapters/AutoResearch/model_adapters.py",
            "BenchmarkAdapters/AutoResearch/search.py",
            "BenchmarkAdapters/OptimizerDesign/__init__.py",
            "BenchmarkAdapters/OptimizerDesign/adapter.py",
            "BenchmarkAdapters/OptimizerDesign/aggregate.py",
            "BenchmarkAdapters/OptimizerDesign/agents/__init__.py",
            "BenchmarkAdapters/OptimizerDesign/agents/ai_scientist.py",
            "BenchmarkAdapters/OptimizerDesign/agents/arbor.py",
            "BenchmarkAdapters/OptimizerDesign/agents/base.py",
            "BenchmarkAdapters/OptimizerDesign/agents/claude_code.py",
            "BenchmarkAdapters/OptimizerDesign/agents/codex.py",
            "BenchmarkAdapters/OptimizerDesign/agents/ear.py",
            "BenchmarkAdapters/OptimizerDesign/agents/mlevolve.py",
            "BenchmarkAdapters/OptimizerDesign/agents/ml_master_2.py",
            "BenchmarkAdapters/OptimizerDesign/baseline.py",
            "BenchmarkAdapters/OptimizerDesign/broker.py",
            "BenchmarkAdapters/OptimizerDesign/evaluator.py",
            "BenchmarkAdapters/OptimizerDesign/protocol.py",
            "BenchmarkAdapters/OptimizerDesign/resource.py",
            "BenchmarkAdapters/OptimizerDesign/revisions.py",
            "BenchmarkAdapters/OptimizerDesign/runtime.py",
            "BenchmarkAdapters/artifacts.py",
            "BenchmarkAdapters/agents.py",
            "BenchmarkAdapters/autonomous_optimization.py",
            "BenchmarkAdapters/contracts.py",
            "BenchmarkAdapters/protocol.py",
            "BenchmarkAdapters/process.py",
            "BenchmarkAdapters/records.py",
            "BenchmarkAdapters/registry.py",
        }
        if set(self.implementation_files) != required:
            raise AdapterError("Optimizer Design evaluator implementation allowlist is incomplete")
        for relative, expected in self.implementation_files.items():
            _validate_relative(relative)
            if not _valid_digest(expected):
                raise AdapterError(f"invalid Optimizer Design implementation digest: {relative}")
            path = ROOT / relative
            if not path.is_file() or path.is_symlink() or sha256_file(path) != expected:
                raise AdapterError(f"Optimizer Design protected implementation drift: {relative}")
        if not all(
            value.strip()
            for value in (self.isolation_policy, self.candidate_policy, self.scoring_policy)
        ):
            raise AdapterError("Optimizer Design evaluator policies must not be empty")

    @classmethod
    def load(cls, path: Path) -> "EvaluatorManifest":
        payload = _load_json(path, "evaluator manifest")
        expected = payload.pop("manifest_digest", None)
        payload["implementation_files"] = dict(payload["implementation_files"])
        manifest = cls(**payload)
        manifest.validate()
        if expected != manifest.digest:
            raise AdapterError(f"Optimizer Design evaluator manifest digest mismatch: {path}")
        return manifest


@dataclass(frozen=True)
class BaselineScoreRecord:
    schema_version: int
    protocol_id: str
    status: str
    baseline_candidate_sha256: str
    source_manifest_digest: str
    data_manifest_digest: str
    environment_manifest_digest: str
    evaluator_manifest_digest: str
    agent_runtime_manifest_digest: str
    environment_lock_digest: str
    held_out_seeds: tuple[int, ...]
    held_out_score_steps: dict[str, int]
    final_score_steps: float | None
    evaluation_record_digests: dict[str, str]
    hardware_fingerprint: str | None
    evidence_files: dict[str, str]
    note: str

    @property
    def digest(self) -> str:
        return hashlib.sha256(canonical_json(self.to_dict())).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["held_out_seeds"] = list(self.held_out_seeds)
        return payload

    def validate(self, evidence_root: Path | None = None) -> None:
        if self.schema_version != 1:
            raise AdapterError("unsupported Optimizer Design baseline score schema")
        if self.protocol_id != "modded-nanogpt-optimizer-design-reconstruction-v1":
            raise AdapterError("Optimizer Design baseline score record uses a different protocol ID")
        if self.status not in {"pending", "completed"}:
            raise AdapterError("Optimizer Design baseline score status must be pending or completed")
        for digest in (
            self.baseline_candidate_sha256,
            self.source_manifest_digest,
            self.data_manifest_digest,
            self.environment_manifest_digest,
            self.evaluator_manifest_digest,
            self.agent_runtime_manifest_digest,
            self.environment_lock_digest,
        ):
            if not _valid_digest(digest):
                raise AdapterError("Optimizer Design baseline score record contains an invalid digest")
        if len(self.held_out_seeds) != 2 or len(set(self.held_out_seeds)) != 2:
            raise AdapterError("Optimizer Design baseline score record requires two held-out seeds")
        if not self.note.strip():
            raise AdapterError("Optimizer Design baseline score record requires an explanatory note")
        if self.status == "pending":
            if (
                self.held_out_score_steps
                or self.final_score_steps is not None
                or self.evaluation_record_digests
                or self.hardware_fingerprint is not None
                or self.evidence_files
            ):
                raise AdapterError("pending Optimizer Design baseline record cannot contain scores")
            return
        keys = {str(seed) for seed in self.held_out_seeds}
        if set(self.held_out_score_steps) != keys or set(self.evaluation_record_digests) != keys:
            raise AdapterError("completed Optimizer Design baseline record is incomplete")
        if any(
            not 1 <= self.held_out_score_steps[str(seed)] <= 3801
            for seed in self.held_out_seeds
        ):
            raise AdapterError("Optimizer Design baseline score_steps is outside the valid range")
        if (
            self.final_score_steps is None
            or not math.isfinite(self.final_score_steps)
            or not 1 <= self.final_score_steps <= 3801
            or not float(self.final_score_steps).is_integer()
        ):
            raise AdapterError("Optimizer Design baseline final score is invalid")
        if any(not _valid_digest(digest) for digest in self.evaluation_record_digests.values()):
            raise AdapterError("Optimizer Design baseline evaluation digest is invalid")
        if self.hardware_fingerprint is None or not _valid_digest(self.hardware_fingerprint):
            raise AdapterError("Optimizer Design baseline hardware fingerprint is invalid")
        expected_evidence = {"baseline-evidence/hardware.json"}
        for index in range(1, len(self.held_out_seeds) + 1):
            expected_evidence.update(
                {
                    f"baseline-evidence/held-out-{index}/candidate.py",
                    f"baseline-evidence/held-out-{index}/evaluation.json",
                    f"baseline-evidence/held-out-{index}/stdout.log",
                }
            )
        if set(self.evidence_files) != expected_evidence:
            raise AdapterError("completed Optimizer Design baseline evidence manifest is incomplete")
        for relative, digest in self.evidence_files.items():
            _validate_relative(relative)
            if not _valid_digest(digest):
                raise AdapterError("Optimizer Design baseline evidence digest is invalid")
        if evidence_root is None:
            return
        evidence_root = evidence_root.resolve()
        for relative, digest in self.evidence_files.items():
            path = evidence_root / relative
            if not path.is_file() or path.is_symlink() or sha256_file(path) != digest:
                raise AdapterError(f"Optimizer Design baseline evidence drift: {relative}")
        hardware = _load_json(
            evidence_root / "baseline-evidence/hardware.json",
            "baseline hardware evidence",
        )
        if hashlib.sha256(canonical_json(hardware)).hexdigest() != self.hardware_fingerprint:
            raise AdapterError("Optimizer Design baseline hardware evidence differs from its fingerprint")
        trajectories: list[dict[int, float]] = []
        for index, seed in enumerate(self.held_out_seeds, 1):
            directory = evidence_root / f"baseline-evidence/held-out-{index}"
            evaluation = _load_json(directory / "evaluation.json", "baseline evaluation evidence")
            evaluation_digest = evaluation.pop("evaluation_digest", None)
            if evaluation_digest != hashlib.sha256(canonical_json(evaluation)).hexdigest():
                raise AdapterError("Optimizer Design baseline evaluation digest mismatch")
            candidate_digest = sha256_file(directory / "candidate.py")
            stdout_digest = sha256_file(directory / "stdout.log")
            if not (
                evaluation.get("status") == "completed"
                and evaluation.get("score_valid") is True
                and evaluation.get("seed") == seed
                and evaluation.get("candidate_sha256") == self.baseline_candidate_sha256
                and candidate_digest == self.baseline_candidate_sha256
                and evaluation.get("stdout_sha256") == stdout_digest
                and evaluation.get("score_steps") == self.held_out_score_steps[str(seed)]
                and evaluation_digest == self.evaluation_record_digests[str(seed)]
            ):
                raise AdapterError("Optimizer Design baseline evaluation evidence is inconsistent")
            raw_trajectory = evaluation.get("validation_trajectory")
            if not isinstance(raw_trajectory, list) or not raw_trajectory:
                raise AdapterError("Optimizer Design baseline trajectory evidence is missing")
            trajectory: dict[int, float] = {}
            for item in raw_trajectory:
                if not isinstance(item, list) or len(item) != 2:
                    raise AdapterError("Optimizer Design baseline trajectory schema is invalid")
                step, loss = int(item[0]), float(item[1])
                if step in trajectory or step < 0 or not math.isfinite(loss) or loss <= 0:
                    raise AdapterError("Optimizer Design baseline trajectory value is invalid")
                trajectory[step] = loss
            trajectories.append(trajectory)
        common_steps = sorted(set.intersection(*(set(item) for item in trajectories)))
        recomputed = 3801
        for step in common_steps:
            mean_loss = sum(item[step] for item in trajectories) / len(trajectories)
            if (3.28 - mean_loss) * len(trajectories) ** 0.5 >= 0.004:
                recomputed = step
                break
        if self.final_score_steps != float(recomputed):
            raise AdapterError("Optimizer Design baseline final score differs from evidence replay")

    @classmethod
    def load(cls, path: Path) -> "BaselineScoreRecord":
        payload = _load_json(path, "baseline score record")
        expected = payload.pop("record_digest", None)
        payload["held_out_seeds"] = tuple(int(seed) for seed in payload["held_out_seeds"])
        payload["held_out_score_steps"] = {
            str(seed): int(score) for seed, score in payload["held_out_score_steps"].items()
        }
        payload["evaluation_record_digests"] = {
            str(seed): str(digest)
            for seed, digest in payload["evaluation_record_digests"].items()
        }
        record = cls(**payload)
        record.validate(path.parent)
        if expected != record.digest:
            raise AdapterError(f"Optimizer Design baseline score digest mismatch: {path}")
        return record


@dataclass(frozen=True)
class OptimizerDesignProtocol:
    protocol_id: str
    source_manifest_path: Path
    source_manifest_digest: str
    data_manifest_path: Path
    data_manifest_digest: str
    environment_manifest_path: Path
    environment_manifest_digest: str
    evaluator_manifest_path: Path
    evaluator_manifest_digest: str
    agent_runtime_manifest_path: Path
    agent_runtime_manifest_digest: str
    environment_lock_path: Path
    environment_lock_digest: str
    baseline_score_record_path: Path
    baseline_score_record_digest: str
    outer_model: str
    reasoning_effort: str
    temperature: float | None
    outer_wall_clock_seconds: int
    outer_seeds: tuple[int, ...]
    development_seed: int
    held_out_seeds: tuple[int, ...]
    target_val_loss: float
    single_run_threshold: float
    significance_margin: float
    max_train_steps: int
    failure_penalty_steps: int
    candidate_timeout_seconds: int
    gpu_count: int
    hardware_policy: str
    cpu_policy: str
    memory_policy: str
    editable_paths: tuple[str, ...]
    formal_status: str
    schema_version: int = 1

    def validate(
        self,
        source_root: Path | None = None,
        data_root: Path | None = None,
        environment_python: Path | None = None,
    ) -> None:
        if self.schema_version not in {1, 2}:
            raise AdapterError("unsupported Optimizer Design protocol schema")
        if self.protocol_id != "modded-nanogpt-optimizer-design-reconstruction-v1":
            raise AdapterError("unexpected Optimizer Design protocol ID")
        source = SourceManifest.load(self.source_manifest_path)
        data = DataManifest.load(self.data_manifest_path)
        environment = EnvironmentManifest.load(self.environment_manifest_path)
        evaluator_manifest = EvaluatorManifest.load(self.evaluator_manifest_path)
        agent_runtime_manifest = AgentRuntimeManifest.load(self.agent_runtime_manifest_path)
        baseline_score = BaselineScoreRecord.load(self.baseline_score_record_path)
        if source.digest != self.source_manifest_digest:
            raise AdapterError("Optimizer Design source manifest differs from protocol")
        if data.digest != self.data_manifest_digest:
            raise AdapterError("Optimizer Design data manifest differs from protocol")
        if environment.digest != self.environment_manifest_digest:
            raise AdapterError("Optimizer Design environment manifest differs from protocol")
        if sha256_file(self.evaluator_manifest_path) != self.evaluator_manifest_digest:
            raise AdapterError("Optimizer Design evaluator manifest differs from protocol")
        if sha256_file(self.agent_runtime_manifest_path) != self.agent_runtime_manifest_digest:
            raise AdapterError("Optimizer Design Agent runtime manifest differs from protocol")
        if not self.environment_lock_path.is_file() or sha256_file(
            self.environment_lock_path
        ) != self.environment_lock_digest:
            raise AdapterError("Optimizer Design environment lock differs from protocol")
        if sha256_file(self.baseline_score_record_path) != self.baseline_score_record_digest:
            raise AdapterError("Optimizer Design baseline score record differs from protocol")
        if (
            baseline_score.baseline_candidate_sha256 != source.baseline_candidate_sha256
            or baseline_score.source_manifest_digest != source.digest
            or baseline_score.data_manifest_digest != data.digest
            or baseline_score.environment_manifest_digest != environment.digest
            or baseline_score.evaluator_manifest_digest != self.evaluator_manifest_digest
            or baseline_score.agent_runtime_manifest_digest != self.agent_runtime_manifest_digest
            or baseline_score.environment_lock_digest != self.environment_lock_digest
            or baseline_score.held_out_seeds != self.held_out_seeds
        ):
            raise AdapterError("Optimizer Design baseline score record differs from frozen assets")
        source.validate(source_root)
        data.validate(data_root)
        environment.validate(environment_python)
        if not self.outer_model.strip() or not self.reasoning_effort.strip():
            raise AdapterError("Optimizer Design legacy model fields must not be empty")
        if self.outer_wall_clock_seconds != 172800:
            raise AdapterError("Optimizer Design outer budget must be 48 hours")
        if len(self.outer_seeds) not in {1, 3} or len(set(self.outer_seeds)) != len(
            self.outer_seeds
        ):
            raise AdapterError("Optimizer Design requires one or three unique outer run IDs")
        if len(self.held_out_seeds) != 2 or len(set(self.held_out_seeds)) != 2:
            raise AdapterError("Optimizer Design requires exactly two held-out seeds")
        if self.development_seed in self.held_out_seeds:
            raise AdapterError("Optimizer Design development seed must be distinct from held-out seeds")
        if (
            self.target_val_loss != 3.28
            or self.single_run_threshold != 3.276
            or self.significance_margin != 0.004
            or self.max_train_steps != 3800
            or self.failure_penalty_steps != 3801
        ):
            raise AdapterError("Optimizer Design scoring policy differs from the frozen reconstruction")
        if self.candidate_timeout_seconds != 7200:
            raise AdapterError("Optimizer Design candidate hard timeout must be two hours")
        if self.gpu_count != 4 or "4xH100-80GB" not in self.hardware_policy:
            raise AdapterError("Optimizer Design reconstruction requires a fixed four-H100 policy")
        if not self.cpu_policy.strip() or not self.memory_policy.strip():
            raise AdapterError("Optimizer Design CPU and memory policies must not be empty")
        if self.editable_paths != (source.editable_path,):
            raise AdapterError("Optimizer Design editable allowlist differs from the source manifest")
        expected_status = (
            "ready" if baseline_score.status == "completed" else "blocked-pending-baseline-record"
        )
        if self.formal_status != expected_status:
            raise AdapterError("Optimizer Design formal readiness differs from the baseline record")

    @property
    def digest(self) -> str:
        self.validate()
        return hashlib.sha256(canonical_json(self.to_dict())).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if self.schema_version == 1:
            payload.pop("schema_version")
        asset_dir = self.source_manifest_path.resolve().parent
        for name in (
            "source_manifest_path",
            "data_manifest_path",
            "environment_manifest_path",
            "evaluator_manifest_path",
            "agent_runtime_manifest_path",
            "environment_lock_path",
            "baseline_score_record_path",
        ):
            payload[name] = _portable_path(getattr(self, name), asset_dir)
        payload["outer_seeds"] = list(self.outer_seeds)
        payload["held_out_seeds"] = list(self.held_out_seeds)
        payload["editable_paths"] = list(self.editable_paths)
        return payload

    @property
    def outer_repetitions(self) -> int:
        return len(self.outer_seeds)

    def protocol_asset_digests(self) -> dict[str, str]:
        return {
            "task_spec": task_spec_digest("optimizer-design"),
            "source_manifest": self.source_manifest_digest,
            "data_manifest": self.data_manifest_digest,
            "environment_manifest": self.environment_manifest_digest,
            "environment_lock": self.environment_lock_digest,
            "evaluator_manifest": self.evaluator_manifest_digest,
            "agent_runtime_manifest": self.agent_runtime_manifest_digest,
            "baseline_score_record": self.baseline_score_record_digest,
        }

    def write(self, path: Path) -> None:
        write_json_exclusive(path, {**self.to_dict(), "protocol_digest": self.digest})

    @classmethod
    def load(
        cls,
        path: Path,
        *,
        source_root: Path | None = None,
        data_root: Path | None = None,
        environment_python: Path | None = None,
    ) -> "OptimizerDesignProtocol":
        payload = _load_json(path, "protocol")
        expected = payload.pop("protocol_digest", None)
        for name in (
            "source_manifest_path",
            "data_manifest_path",
            "environment_manifest_path",
            "evaluator_manifest_path",
            "agent_runtime_manifest_path",
            "environment_lock_path",
            "baseline_score_record_path",
        ):
            payload[name] = _resolve_portable_path(str(payload[name]), path.parent)
        payload["outer_seeds"] = tuple(int(seed) for seed in payload["outer_seeds"])
        payload["held_out_seeds"] = tuple(int(seed) for seed in payload["held_out_seeds"])
        payload["editable_paths"] = tuple(str(item) for item in payload["editable_paths"])
        protocol = cls(**payload)
        protocol.validate(source_root, data_root, environment_python)
        if expected != protocol.digest:
            raise AdapterError(f"Optimizer Design protocol digest mismatch: {path}")
        return protocol

    @property
    def formal_baseline_ready(self) -> bool:
        return self.formal_status == "ready" and (
            BaselineScoreRecord.load(self.baseline_score_record_path).status == "completed"
        )

    def require_formal_contract(self) -> None:
        if (
            self.schema_version != 2
            or self.outer_model != "configured-by-model-track"
            or self.reasoning_effort != "configured-by-model-track"
            or self.temperature is not None
        ):
            raise AdapterError(
                "formal Optimizer Design requires a reviewed schema-v2 model-track protocol candidate"
            )

    def require_formal_ready(self) -> None:
        self.require_formal_contract()
        if not self.formal_baseline_ready:
            raise AdapterError(
                "formal Optimizer Design runs are blocked pending the frozen two-seed baseline record"
            )


def build_protocol(
    asset_dir: Path = DEFAULT_ASSET_DIR,
    *,
    outer_repetitions: int = 1,
) -> OptimizerDesignProtocol:
    if outer_repetitions not in {1, 3}:
        raise AdapterError("Optimizer Design outer_repetitions must be 1 or 3")
    asset_dir = asset_dir.resolve()
    source_path = asset_dir / "source_manifest.json"
    data_path = asset_dir / "data_manifest.json"
    environment_path = asset_dir / "environment_manifest.json"
    evaluator_path = asset_dir / "evaluator_manifest.json"
    agent_runtime_path = asset_dir / "agent_runtime_manifest.json"
    baseline_score_path = asset_dir / "baseline_score_record.json"
    source = SourceManifest.load(source_path)
    data = DataManifest.load(data_path)
    environment = EnvironmentManifest.load(environment_path)
    baseline_score = BaselineScoreRecord.load(baseline_score_path)
    protocol = OptimizerDesignProtocol(
        protocol_id="modded-nanogpt-optimizer-design-reconstruction-v1",
        source_manifest_path=source_path,
        source_manifest_digest=source.digest,
        data_manifest_path=data_path,
        data_manifest_digest=data.digest,
        environment_manifest_path=environment_path,
        environment_manifest_digest=environment.digest,
        evaluator_manifest_path=evaluator_path,
        evaluator_manifest_digest=sha256_file(evaluator_path),
        agent_runtime_manifest_path=agent_runtime_path,
        agent_runtime_manifest_digest=sha256_file(agent_runtime_path),
        environment_lock_path=ROOT / "optimizer-design/uv.lock",
        environment_lock_digest=sha256_file(ROOT / "optimizer-design/uv.lock"),
        baseline_score_record_path=baseline_score_path,
        baseline_score_record_digest=sha256_file(baseline_score_path),
        outer_model="configured-by-model-track",
        reasoning_effort="configured-by-model-track",
        temperature=None,
        outer_wall_clock_seconds=172800,
        outer_seeds=tuple(range(outer_repetitions)),
        development_seed=42,
        held_out_seeds=(314159, 271828),
        target_val_loss=3.28,
        single_run_threshold=3.276,
        significance_margin=0.004,
        max_train_steps=3800,
        failure_penalty_steps=3801,
        candidate_timeout_seconds=7200,
        gpu_count=4,
        hardware_policy="one exclusive 4xH100-80GB host allocation per evaluation; not comparable to Arbor 4xA100",
        cpu_policy="one explicit host cpuset reused across all seven Agents and recorded in manifests",
        memory_policy="one explicit RLIMIT_AS of at least 64 GiB reused across all seven Agents",
        editable_paths=(source.editable_path,),
        formal_status=(
            "ready" if baseline_score.status == "completed" else "blocked-pending-baseline-record"
        ),
        schema_version=2,
    )
    protocol.validate()
    return protocol


__all__ = [
    "DEFAULT_ASSET_DIR",
    "DEFAULT_DATA_ROOT",
    "DEFAULT_ENVIRONMENT_PYTHON",
    "DEFAULT_SOURCE_ROOT",
    "BaselineScoreRecord",
    "DataManifest",
    "EnvironmentManifest",
    "EvaluatorManifest",
    "OptimizerDesignProtocol",
    "SourceManifest",
    "build_protocol",
]
