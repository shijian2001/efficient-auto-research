"""Explicit, review-gated promotion of real baseline evaluator evidence."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from .AutoResearch.protocol import BaselineScoreRecord as AutoResearchBaseline
from .contracts import AdapterError
from .OptimizerDesign.protocol import BaselineScoreRecord as OptimizerBaseline
from .protocol import sha256_file, write_json_exclusive


def promote_baseline_record(
    *,
    benchmark_id: str,
    candidate_path: Path,
    destination_path: Path,
    expected_pending_sha256: str,
    acknowledge_reviewed: bool,
) -> dict[str, str]:
    if not acknowledge_reviewed:
        raise AdapterError("baseline promotion requires explicit --acknowledge-reviewed")
    candidate_path = candidate_path.resolve()
    destination_path = destination_path.resolve()
    if not destination_path.is_file() or destination_path.is_symlink():
        raise AdapterError("baseline promotion destination must be the existing pending record")
    actual_pending = sha256_file(destination_path)
    if actual_pending != expected_pending_sha256:
        raise AdapterError(
            "pending baseline digest differs from reviewed input: "
            f"expected={expected_pending_sha256} actual={actual_pending}"
        )
    if benchmark_id == "autoresearch-architecture":
        current = AutoResearchBaseline.load(destination_path)
        candidate = AutoResearchBaseline.load(candidate_path)
    elif benchmark_id == "optimizer-design":
        current = OptimizerBaseline.load(destination_path)
        candidate = OptimizerBaseline.load(candidate_path)
    else:
        raise AdapterError(f"baseline promotion is unsupported for {benchmark_id}")
    if current.status != "pending" or candidate.status != "completed":
        raise AdapterError("baseline promotion requires pending destination and completed candidate")
    evidence_source = candidate_path.parent / "baseline-evidence"
    evidence_destination = destination_path.parent / "baseline-evidence"
    if not evidence_source.is_dir() or evidence_source.is_symlink():
        raise AdapterError("completed baseline candidate evidence directory is missing")
    if evidence_destination.exists() or evidence_destination.is_symlink():
        raise AdapterError("refusing to overwrite promoted baseline evidence")
    backup = destination_path.parent / f"{destination_path.stem}.pending-{actual_pending}.json"
    write_json_exclusive(backup, __import__("json").loads(destination_path.read_text(encoding="utf-8")))
    staging = Path(tempfile.mkdtemp(prefix=".baseline-evidence.", dir=destination_path.parent))
    staged_evidence = staging / "baseline-evidence"
    temporary_record: Path | None = None
    evidence_promoted = False
    try:
        shutil.copytree(evidence_source, staged_evidence, symlinks=False)
        if benchmark_id == "autoresearch-architecture":
            candidate.validate(candidate_path.parent)
        else:
            candidate.validate(candidate_path.parent)
        os.replace(staged_evidence, evidence_destination)
        evidence_promoted = True
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{destination_path.name}.", suffix=".tmp", dir=destination_path.parent
        )
        temporary_record = Path(temporary_name)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(candidate_path.read_bytes())
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_record, destination_path)
        temporary_record = None
    except Exception:
        if evidence_promoted and evidence_destination.is_dir():
            shutil.rmtree(evidence_destination)
        raise
    finally:
        if temporary_record is not None:
            temporary_record.unlink(missing_ok=True)
        shutil.rmtree(staging, ignore_errors=True)
    return {
        "benchmark_id": benchmark_id,
        "previous_pending_sha256": actual_pending,
        "promoted_record_sha256": sha256_file(destination_path),
        "pending_backup_path": str(backup),
        "evidence_path": str(evidence_destination),
        "next_step": "review and regenerate the protocol so it binds the promoted record digest",
    }


__all__ = ["promote_baseline_record"]
