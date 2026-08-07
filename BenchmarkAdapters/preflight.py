"""Stable protocol-level preflight for the seven-Agent benchmark matrix."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .MLEBenchLite.campaign import build_mle_protocol, validate_mle_protocol
from .readiness import ReadinessLevel
from .registry import AGENTS, ROOT
from .TerminalAO.protocol import TerminalAOProtocol
from .TerminalAO.split import FrozenSplit


DEFAULT_AO_PROTOCOL = ROOT / "terminal-bench-2/ao_protocol/protocol.json"
DEFAULT_MLE_DATA = ROOT / "mle-bench-data"


def _executable_available(command: str) -> bool:
    path = Path(command)
    if path.is_absolute() or "/" in command:
        return path.is_file() and os.access(path, os.X_OK)
    return shutil.which(command) is not None


def _git_state(path: Path) -> tuple[str | None, bool | None]:
    commit = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    dirty = subprocess.run(
        ["git", "-C", str(path), "status", "--porcelain", "--untracked-files=no"],
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
    return bool(
        payload.get("agent") == agent
        and payload.get("mode") == mode
        and payload.get("protocol_digest") == protocol_digest
        and payload.get("status") == "completed"
        and payload.get("score_valid") is True
        and isinstance(payload.get("score"), (int, float))
        and isinstance(artifact_digest, str)
        and len(artifact_digest) == 64
        and all(character in "0123456789abcdef" for character in artifact_digest)
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

    for key, spec in AGENTS.items():
        commit, dirty = _git_state(spec.install_path)
        source_ready = spec.install_path.is_dir() and commit is not None
        environment_ready = _executable_available(spec.version_command[0])
        modes: dict[str, Any] = {}
        for mode, protocol_record in (
            ("mle", mle_protocol_record),
            ("terminal-ao", ao_protocol_record),
        ):
            protocol_valid = bool(protocol_record.get("valid"))
            if not source_ready:
                level = ReadinessLevel.NOT_READY
                detail = "source checkout or commit identity is missing"
                evidence = None
            elif not environment_ready:
                level = ReadinessLevel.SOURCE_READY
                detail = "source exists but the locked Agent runtime is unavailable"
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
                "formal_launch_allowed": bool(
                    level is ReadinessLevel.FORMAL_PROTOCOL_READY
                    and dirty is False
                    and adapter_dirty is False
                ),
            }
        checks[key] = {
            "display_name": spec.display_name,
            "source_path": str(spec.install_path),
            "source_commit": commit,
            "source_dirty": dirty,
            "source_ready": source_ready,
            "environment_ready": environment_ready,
            "backends": {
                "mle": spec.mle_backend,
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
        "protocols": {"mle": mle_protocol_record, "terminal_ao": ao_protocol_record},
        "comparison_policy": {
            "mle_and_terminal_ao_are_separate_scorecards": True,
            "terminal_direct_89_is_excluded": True,
            "minimum_seeds": 3,
            "missing_or_invalid_runs_count_as_zero": True,
        },
    }


__all__ = ["DEFAULT_AO_PROTOCOL", "DEFAULT_MLE_DATA", "collect_preflight"]
