"""Stable protocol-level preflight for the seven-Agent benchmark matrix."""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .AutoResearch.protocol import AutoResearchProtocol, DEFAULT_ASSET_DIR as DEFAULT_AUTORESEARCH_ASSETS
from .MLEBenchLite.campaign import build_mle_protocol, validate_mle_protocol
from .OptimizerDesign.protocol import (
    DEFAULT_ASSET_DIR as DEFAULT_OPTIMIZER_DESIGN_ASSETS,
    DEFAULT_DATA_ROOT as DEFAULT_OPTIMIZER_DESIGN_DATA,
    DEFAULT_ENVIRONMENT_PYTHON as DEFAULT_OPTIMIZER_DESIGN_ENVIRONMENT_PYTHON,
    DEFAULT_SOURCE_ROOT as DEFAULT_OPTIMIZER_DESIGN_SOURCE,
    OptimizerDesignProtocol,
)
from .readiness import ReadinessLevel
from .protocol import sha256_file
from .registry import AGENTS, ROOT
from .TerminalAO.protocol import TerminalAOProtocol
from .TerminalAO.split import FrozenSplit
from .thin_registry import THIN_CLASSIFICATIONS, UPSTREAM_REVISIONS


DEFAULT_AO_PROTOCOL = ROOT / "terminal-bench-2/ao_protocol/protocol.json"
DEFAULT_MLE_DATA = ROOT / "mle-bench-data"
DEFAULT_AUTORESEARCH_PROTOCOL = DEFAULT_AUTORESEARCH_ASSETS / "protocol.json"
DEFAULT_AUTORESEARCH_PREPARED = ROOT / "autoresearch/.runtime/home/.cache/autoresearch"
DEFAULT_AUTORESEARCH_ENVIRONMENT_PYTHON = ROOT / "autoresearch/.venv/bin/python"
DEFAULT_AUTORESEARCH_KERNEL_CACHE = (
    ROOT / "autoresearch/.runtime/home/.cache/huggingface/hub/models--varunneal--flash-attention-3"
)
DEFAULT_OPTIMIZER_DESIGN_PROTOCOL = DEFAULT_OPTIMIZER_DESIGN_ASSETS / "protocol.json"


def _executable_available(command: str) -> bool:
    path = Path(command)
    if path.is_absolute() or "/" in command:
        return path.is_file() and os.access(path, os.X_OK)
    return shutil.which(command) is not None


def _autoresearch_runtime_available(agent: str) -> bool:
    if agent == "arbor":
        executable = ROOT / "baselines/Arbor/.venv/bin/arbor"
        return executable.is_file() and os.access(executable, os.X_OK)
    if agent in {"ai-scientist", "ml-master-2"}:
        return False
    executable, source_root, module = {
        "ear": ROOT / "BenchmarkAdapters/environments/agents/ear-autoresearch/.venv/bin/python",
        "mlevolve": ROOT
        / "BenchmarkAdapters/environments/agents/mlevolve-autoresearch/.venv/bin/python",
        "codex": Path(shutil.which("codex") or ""),
        "claude-code": Path(shutil.which("claude") or ""),
    }[agent], {
        "ear": ROOT / "BenchmarkAdapters/environments/agents/ear-autoresearch/.venv/agent-source",
        "mlevolve": ROOT
        / "BenchmarkAdapters/environments/agents/mlevolve-autoresearch/.venv/agent-source",
        "codex": None,
        "claude-code": None,
    }[agent], {
        "ear": "agent.engine.thompson",
        "mlevolve": "engine.agent_search",
        "codex": None,
        "claude-code": None,
    }[agent]
    if not executable.is_file() or not os.access(executable, os.X_OK):
        return False
    if module is None:
        completed = subprocess.run(
            [str(executable), "--version"],
            capture_output=True,
            text=True,
            check=False,
        )
        return completed.returncode == 0
    environment = os.environ.copy()
    if source_root is not None:
        environment["PYTHONPATH"] = f"{ROOT}:{source_root}"
    completed = subprocess.run(
        [str(executable), "-c", f"import {module}"],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.returncode == 0


def _python_runtime_available(path: Path) -> bool:
    path = path.expanduser().absolute()
    if not path.is_file() or not os.access(path, os.X_OK):
        return False
    completed = subprocess.run(
        [str(path), "-c", "import platform; print(platform.python_version())"],
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.returncode == 0 and completed.stdout.strip().startswith("3.10.")


def _git_state(path: Path) -> tuple[str | None, bool | None]:
    path = path.resolve()
    top_level = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
    )
    if top_level.returncode:
        return None, None
    git_root = Path(top_level.stdout.strip()).resolve()
    try:
        relative = path.relative_to(git_root)
    except ValueError:
        return None, None
    treeish = "HEAD" if relative == Path(".") else f"HEAD:{relative.as_posix()}"
    commit = subprocess.run(
        ["git", "-C", str(git_root), "rev-parse", treeish],
        capture_output=True,
        text=True,
        check=False,
    )
    dirty = subprocess.run(
        [
            "git",
            "-C",
            str(git_root),
            "status",
            "--porcelain",
            "--untracked-files=normal",
            "--",
            relative.as_posix(),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if commit.returncode or dirty.returncode:
        return None, None
    return commit.stdout.strip(), bool(dirty.stdout.strip())


def _valid_scored_evidence(
    path: Path,
    *,
    agent: str,
    mode: str,
    protocol_digest: str,
) -> bool:
    if not path.is_file():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    artifact_digest = payload.get("artifact_sha256")
    common_valid = bool(
        payload.get("agent") == agent
        and payload.get("mode") == mode
        and payload.get("protocol_digest") == protocol_digest
    )
    if not common_valid:
        return False
    if mode == "autoresearch":
        evidence_kind = payload.get("evidence_kind")
        if evidence_kind == "real_smoke":
            if payload.get("non_comparable") is not True:
                return False
            result_path = Path(str(payload.get("result_path", "")))
            return _valid_autoresearch_result(
                result_path,
                agent=agent,
                protocol_digest=protocol_digest,
                require_formal=False,
            )
        if evidence_kind == "formal_campaign":
            aggregate_path = Path(str(payload.get("aggregate_path", "")))
            return _valid_autoresearch_aggregate(
                aggregate_path,
                agent=agent,
                protocol_digest=protocol_digest,
            )
        return False
    if mode == "optimizer-design":
        return False
    return bool(
        payload.get("status") == "completed"
        and payload.get("score_valid") is True
        and isinstance(payload.get("score"), (int, float))
        and isinstance(artifact_digest, str)
        and len(artifact_digest) == 64
        and all(character in "0123456789abcdef" for character in artifact_digest)
    )


def _load_object(path: Path) -> dict[str, Any] | None:
    if not path.is_file() or path.is_symlink():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _valid_autoresearch_result(
    path: Path,
    *,
    agent: str,
    protocol_digest: str,
    require_formal: bool,
) -> bool:
    payload = _load_object(path)
    if payload is None:
        return False
    metrics = payload.get("metrics")
    if not isinstance(metrics, dict):
        return False
    held_out = metrics.get("held_out_val_bpb")
    score = payload.get("score")
    if (
        payload.get("mode") != "autoresearch"
        or payload.get("agent") != agent
        or payload.get("protocol_digest") != protocol_digest
        or payload.get("status") != "completed"
        or payload.get("score_valid") is not True
        or not isinstance(score, (int, float))
        or not math.isfinite(float(score))
        or not isinstance(held_out, list)
        or len(held_out) != 2
        or any(not isinstance(value, (int, float)) or not math.isfinite(float(value)) for value in held_out)
        or not math.isclose(float(score), sum(float(value) for value in held_out) / 2, abs_tol=1e-12)
    ):
        return False
    artifact_path = Path(str(payload.get("artifact_path", "")))
    artifact_digest = payload.get("artifact_sha256")
    if (
        not artifact_path.is_file()
        or artifact_path.is_symlink()
        or not isinstance(artifact_digest, str)
        or sha256_file(artifact_path) != artifact_digest
    ):
        return False
    manifest = _load_object(path.parent / "manifest.json")
    if manifest is None:
        return False
    manifest_digest = manifest.pop("manifest_digest", None)
    from .protocol import canonical_json

    actual_manifest_digest = hashlib.sha256(canonical_json(manifest)).hexdigest()
    if (
        manifest_digest != actual_manifest_digest
        or payload.get("manifest_digest") != actual_manifest_digest
        or manifest.get("protocol_digest") != protocol_digest
        or manifest.get("agent") != agent
        or (require_formal and manifest.get("formal") is not True)
    ):
        return False
    final_root = path.parent / "final-evaluations"
    records = sorted(final_root.glob("held-out-*/evaluation.json"))
    if len(records) != 2:
        return False
    evaluations = [_load_object(record) for record in records]
    return all(
        evaluation is not None
        and evaluation.get("status") == "completed"
        and evaluation.get("score_valid") is True
        for evaluation in evaluations
    )


def _valid_autoresearch_aggregate(
    path: Path,
    *,
    agent: str,
    protocol_digest: str,
) -> bool:
    payload = _load_object(path)
    if payload is None:
        return False
    cells = payload.get("seed_cells")
    if (
        payload.get("mode") != "autoresearch"
        or payload.get("agent") != agent
        or payload.get("protocol_digest") != protocol_digest
        or payload.get("formal_avg_at_3_valid") is not True
        or not isinstance(cells, list)
        or len(cells) != 3
    ):
        return False
    result_paths = [Path(str(cell.get("result_path", ""))) for cell in cells if isinstance(cell, dict)]
    return len(result_paths) == 3 and all(
        _valid_autoresearch_result(
            result_path,
            agent=agent,
            protocol_digest=protocol_digest,
            require_formal=True,
        )
        for result_path in result_paths
    )


def _evidence_for(
    agent: str,
    mode: str,
    protocol_digest: str,
    evidence_root: Path,
) -> tuple[ReadinessLevel, str | None]:
    formal = evidence_root / mode / agent / "formal.json"
    smoke = evidence_root / mode / agent / "real-smoke.json"
    if _valid_scored_evidence(
        formal,
        agent=agent,
        mode=mode,
        protocol_digest=protocol_digest,
    ):
        return ReadinessLevel.FORMAL_PROTOCOL_READY, str(formal)
    if _valid_scored_evidence(
        smoke,
        agent=agent,
        mode=mode,
        protocol_digest=protocol_digest,
    ):
        return ReadinessLevel.REAL_SMOKE_READY, str(smoke)
    return ReadinessLevel.COMMAND_READY, None


def collect_preflight(
    *,
    mle_data_root: Path = DEFAULT_MLE_DATA,
    ao_protocol_path: Path = DEFAULT_AO_PROTOCOL,
    autoresearch_protocol_path: Path = DEFAULT_AUTORESEARCH_PROTOCOL,
    autoresearch_prepared_root: Path = DEFAULT_AUTORESEARCH_PREPARED,
    autoresearch_environment_python: Path = DEFAULT_AUTORESEARCH_ENVIRONMENT_PYTHON,
    autoresearch_kernel_cache_root: Path = DEFAULT_AUTORESEARCH_KERNEL_CACHE,
    optimizer_design_protocol_path: Path = DEFAULT_OPTIMIZER_DESIGN_PROTOCOL,
    optimizer_design_source_root: Path = DEFAULT_OPTIMIZER_DESIGN_SOURCE,
    optimizer_design_data_root: Path = DEFAULT_OPTIMIZER_DESIGN_DATA,
    optimizer_design_environment_python: Path = DEFAULT_OPTIMIZER_DESIGN_ENVIRONMENT_PYTHON,
    evidence_root: Path = ROOT / "benchmark-evidence/readiness",
) -> dict[str, Any]:
    checks: dict[str, dict[str, Any]] = {}
    adapter_commit, adapter_dirty = _git_state(ROOT)
    try:
        mle_protocol = build_mle_protocol()
        validate_mle_protocol(mle_protocol, mle_data_root)
        mle_protocol_record: dict[str, Any] = {
            "valid": True,
            "protocol_id": mle_protocol.protocol_id,
            "protocol_digest": mle_protocol.digest,
            "task_count": len(mle_protocol.task_ids),
            "asset_digests": dict(mle_protocol.asset_digests),
            "data_root": str(mle_data_root.resolve()),
        }
    except Exception as exc:
        mle_protocol_record = {"valid": False, "error": f"{type(exc).__name__}: {exc}"}
    try:
        ao_protocol = TerminalAOProtocol.load(ao_protocol_path)
        split = FrozenSplit.load(ao_protocol.split_path)
        ao_protocol_record: dict[str, Any] = {
            "valid": True,
            "protocol_id": ao_protocol.protocol_id,
            "protocol_digest": ao_protocol.digest,
            "task_count": {"dev": len(split.dev), "held_out_test": len(split.test)},
            "asset_digests": {
                "dataset": ao_protocol.dataset_digest,
                "split": ao_protocol.split_digest,
                "baseline_manifest": ao_protocol.baseline_manifest_digest,
                "harbor_lock": ao_protocol.harbor_lock_digest,
            },
            "harbor_version": ao_protocol.harbor_version,
            "direct_89_comparable": False,
        }
    except Exception as exc:
        ao_protocol_record = {"valid": False, "error": f"{type(exc).__name__}: {exc}"}
    try:
        autoresearch_protocol = AutoResearchProtocol.load(
            autoresearch_protocol_path,
            prepared_root=autoresearch_prepared_root,
            kernel_cache_root=autoresearch_kernel_cache_root,
        )
        autoresearch_protocol_record: dict[str, Any] = {
            "valid": True,
            "protocol_id": autoresearch_protocol.protocol_id,
            "protocol_digest": autoresearch_protocol.digest,
            "candidate_training_seconds": autoresearch_protocol.candidate_training_seconds,
            "outer_wall_clock_seconds": autoresearch_protocol.outer_wall_clock_seconds,
            "outer_seeds": list(autoresearch_protocol.outer_seeds),
            "held_out_evaluations": 2,
            "baseline_score_status": (
                "completed" if autoresearch_protocol.formal_baseline_ready else "pending"
            ),
            "formal_baseline_ready": autoresearch_protocol.formal_baseline_ready,
            "editable_paths": list(autoresearch_protocol.editable_paths),
            "prepared_root": str(autoresearch_prepared_root.resolve()),
            "environment_python": str(autoresearch_environment_python.expanduser().absolute()),
            "benchmark_environment_ready": _python_runtime_available(
                autoresearch_environment_python
            ),
            "kernel_cache_root": str(autoresearch_kernel_cache_root.resolve()),
            "asset_digests": {
                "baseline_manifest": autoresearch_protocol.baseline_manifest_digest,
                "prepared_manifest": autoresearch_protocol.prepared_manifest_digest,
                "kernel_cache_manifest": autoresearch_protocol.kernel_cache_manifest_digest,
                "evaluator_manifest": autoresearch_protocol.evaluator_manifest_digest,
                "seed_policy": autoresearch_protocol.seed_policy_digest,
                "environment_lock": autoresearch_protocol.environment_lock_digest,
                "baseline_score_record": autoresearch_protocol.baseline_score_record_digest,
            },
        }
    except Exception as exc:
        autoresearch_protocol_record = {
            "valid": False,
            "error": f"{type(exc).__name__}: {exc}",
            "prepared_root": str(autoresearch_prepared_root.resolve()),
            "environment_python": str(autoresearch_environment_python.expanduser().absolute()),
            "benchmark_environment_ready": _python_runtime_available(
                autoresearch_environment_python
            ),
            "kernel_cache_root": str(autoresearch_kernel_cache_root.resolve()),
        }
    try:
        optimizer_design_protocol = OptimizerDesignProtocol.load(
            optimizer_design_protocol_path,
            source_root=optimizer_design_source_root,
            data_root=optimizer_design_data_root,
            environment_python=optimizer_design_environment_python,
        )
        optimizer_design_protocol_record: dict[str, Any] = {
            "valid": True,
            "protocol_id": optimizer_design_protocol.protocol_id,
            "protocol_digest": optimizer_design_protocol.digest,
            "source_root": str(optimizer_design_source_root.resolve()),
            "data_root": str(optimizer_design_data_root.resolve()),
            "environment_python": str(
                optimizer_design_environment_python.expanduser().absolute()
            ),
            "benchmark_environment_ready": True,
            "outer_wall_clock_seconds": optimizer_design_protocol.outer_wall_clock_seconds,
            "outer_seeds": list(optimizer_design_protocol.outer_seeds),
            "held_out_evaluations": len(optimizer_design_protocol.held_out_seeds),
            "editable_paths": list(optimizer_design_protocol.editable_paths),
            "formal_baseline_ready": optimizer_design_protocol.formal_baseline_ready,
            "formal_status": optimizer_design_protocol.formal_status,
            "asset_digests": {
                "source_manifest": optimizer_design_protocol.source_manifest_digest,
                "data_manifest": optimizer_design_protocol.data_manifest_digest,
                "environment_manifest": optimizer_design_protocol.environment_manifest_digest,
                "evaluator_manifest": optimizer_design_protocol.evaluator_manifest_digest,
                "agent_runtime_manifest": optimizer_design_protocol.agent_runtime_manifest_digest,
                "environment_lock": optimizer_design_protocol.environment_lock_digest,
                "baseline_score_record": optimizer_design_protocol.baseline_score_record_digest,
            },
        }
    except Exception as exc:
        optimizer_design_protocol_record = {
            "valid": False,
            "error": f"{type(exc).__name__}: {exc}",
            "source_root": str(optimizer_design_source_root.resolve()),
            "data_root": str(optimizer_design_data_root.resolve()),
            "environment_python": str(
                optimizer_design_environment_python.expanduser().absolute()
            ),
            "benchmark_environment_ready": False,
            "formal_baseline_ready": False,
        }

    for key, spec in AGENTS.items():
        commit, dirty = _git_state(spec.install_path)
        source_ready = spec.install_path.is_dir() and commit is not None
        environment_ready = _executable_available(spec.version_command[0])
        autoresearch_environment_ready = _autoresearch_runtime_available(key)
        modes: dict[str, Any] = {}
        for mode, protocol_record in (
            ("mle", mle_protocol_record),
            ("autoresearch", autoresearch_protocol_record),
            ("optimizer-design", optimizer_design_protocol_record),
            ("terminal-ao", ao_protocol_record),
        ):
            benchmark_id = {
                "mle": "mle-bench-lite",
                "autoresearch": "autoresearch-architecture",
                "optimizer-design": "optimizer-design",
                "terminal-ao": "terminal-bench-ao",
            }[mode]
            classification = THIN_CLASSIFICATIONS.get(key, {}).get(benchmark_id)
            reviewed_original_source = (
                key not in UPSTREAM_REVISIONS
                or (
                    commit == UPSTREAM_REVISIONS[key]
                    and dirty is False
                )
            )
            protocol_valid = bool(protocol_record.get("valid"))
            agent_environment_ready = (
                autoresearch_environment_ready
                if mode in {"autoresearch", "optimizer-design"}
                else environment_ready
            )
            benchmark_environment_ready = (
                bool(protocol_record.get("benchmark_environment_ready"))
                if mode in {"autoresearch", "optimizer-design"}
                else True
            )
            if classification == "unsupported":
                level = ReadinessLevel.NOT_READY
                detail = "unsupported: no official thin adapter entrypoint"
                evidence = None
            elif not reviewed_original_source:
                level = ReadinessLevel.NOT_READY
                detail = "original thin adapter requires the reviewed clean upstream revision"
                evidence = None
            elif not source_ready:
                level = ReadinessLevel.NOT_READY
                detail = "source checkout or commit identity is missing"
                evidence = None
            elif not agent_environment_ready:
                level = ReadinessLevel.SOURCE_READY
                detail = "source exists but the locked Agent runtime is unavailable"
                evidence = None
            elif not benchmark_environment_ready:
                level = ReadinessLevel.ENVIRONMENT_READY
                detail = "Agent runtime exists but the locked benchmark runtime is unavailable"
                evidence = None
            elif not protocol_valid:
                level = ReadinessLevel.ENVIRONMENT_READY
                detail = "Agent runtime exists but the frozen benchmark protocol failed validation"
                evidence = None
            else:
                level, evidence = _evidence_for(
                    key,
                    mode,
                    str(protocol_record["protocol_digest"]),
                    evidence_root,
                )
                detail = (
                    "formal evidence record exists"
                    if level is ReadinessLevel.FORMAL_PROTOCOL_READY
                    else "real smoke evidence record exists"
                    if level is ReadinessLevel.REAL_SMOKE_READY
                    else "native command contract and frozen protocol validate; no durable real smoke evidence"
                )
            modes[mode] = {
                "level": level.name.lower(),
                "level_value": int(level),
                "detail": detail,
                "evidence_path": evidence,
                "thin_adapter_classification": classification,
                "reviewed_original_source": reviewed_original_source,
                "formal_launch_allowed": bool(
                    level >= ReadinessLevel.COMMAND_READY
                    and dirty is False
                    and adapter_dirty is False
                    and (
                        mode not in {"autoresearch", "optimizer-design"}
                        or protocol_record.get("formal_baseline_ready") is True
                    )
                ),
            }
        checks[key] = {
            "display_name": spec.display_name,
            "source_path": str(spec.install_path),
            "source_commit": commit,
            "source_dirty": dirty,
            "source_ready": source_ready,
            "environment_ready": environment_ready,
            "autoresearch_environment_ready": autoresearch_environment_ready,
            "backends": {
                "mle": spec.mle_backend,
                "autoresearch": spec.autoresearch_backend,
                "optimizer_design": spec.optimizer_design_backend,
                "terminal_ao": spec.terminal_ao_backend,
                "terminal_direct_smoke": spec.terminal_direct_smoke_backend,
            },
            "modes": modes,
        }
    return {
        "schema_version": 1,
        "adapter_commit": adapter_commit,
        "adapter_source_dirty": adapter_dirty,
        "formal_source_clean": adapter_dirty is False,
        "agents": checks,
        "protocols": {
            "mle": mle_protocol_record,
            "autoresearch": autoresearch_protocol_record,
            "optimizer_design": optimizer_design_protocol_record,
            "terminal_ao": ao_protocol_record,
        },
        "comparison_policy": {
            "mle_and_terminal_ao_are_separate_scorecards": True,
            "autoresearch_is_a_separate_scorecard": True,
            "optimizer_design_is_a_separate_scorecard": True,
            "terminal_direct_89_is_excluded": True,
            "minimum_seeds": 3,
            "mle_and_terminal_missing_or_invalid_runs_count_as_zero": True,
            "autoresearch_missing_or_invalid_runs_withhold_avg_at_3": True,
        },
    }


__all__ = [
    "DEFAULT_AO_PROTOCOL",
    "DEFAULT_AUTORESEARCH_PREPARED",
    "DEFAULT_AUTORESEARCH_KERNEL_CACHE",
    "DEFAULT_AUTORESEARCH_ENVIRONMENT_PYTHON",
    "DEFAULT_AUTORESEARCH_PROTOCOL",
    "DEFAULT_MLE_DATA",
    "DEFAULT_OPTIMIZER_DESIGN_DATA",
    "DEFAULT_OPTIMIZER_DESIGN_ENVIRONMENT_PYTHON",
    "DEFAULT_OPTIMIZER_DESIGN_PROTOCOL",
    "DEFAULT_OPTIMIZER_DESIGN_SOURCE",
    "collect_preflight",
]
