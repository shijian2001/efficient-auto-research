"""Host-owned candidate records for the Arbor MLE-Bench adapter.

The store binds an Arbor-selected candidate artifact to the local evaluation
receipt that produced it. It deliberately does not rank records or interpret
their metrics: candidate selection remains Arbor behavior.
"""

from __future__ import annotations

import json
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .common import AdapterError, sha256_file


STATE_SCHEMA_VERSION = 1
_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def _component(value: str, *, label: str) -> str:
    normalized = str(value).strip()
    if not normalized or not _SAFE_COMPONENT.fullmatch(normalized):
        raise AdapterError(f"invalid {label} for MLE state store")
    return normalized


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AdapterError(f"invalid MLE state record: {path}") from exc
    if not isinstance(value, dict):
        raise AdapterError(f"MLE state record must be an object: {path}")
    return value


def state_root_from_environment() -> Path:
    value = os.environ.get("ARBOR_MLE_STATE_ROOT", "").strip()
    if not value:
        raise AdapterError("ARBOR_MLE_STATE_ROOT is required for MLE artifact verification")
    return Path(value).resolve()


def run_id_from_environment() -> str:
    value = os.environ.get("ARBOR_MLE_RUN_ID", "").strip()
    if not value:
        raise AdapterError("ARBOR_MLE_RUN_ID is required for MLE artifact verification")
    return _component(value, label="run id")


def candidate_record_path(
    root: Path,
    *,
    run_id: str,
    competition_id: str,
    node_id: str,
    attempt_id: str,
) -> Path:
    return (
        root
        / _component(run_id, label="run id")
        / _component(competition_id, label="competition id")
        / "candidates"
        / _component(node_id, label="node id")
        / f"{_component(attempt_id, label='attempt id')}.json"
    )


def _write_exclusive(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode("utf-8")
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    except FileExistsError as exc:
        raise AdapterError(f"MLE candidate state already exists: {path}") from exc
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise


def publish_receipt(
    *,
    root: Path,
    run_id: str,
    competition_id: str,
    node_id: str,
    attempt_id: str,
    workspace: Path,
    receipt_path: Path,
    solution_relative_path: str,
    submission_relative_path: str,
) -> dict[str, Any]:
    """Validate a worktree receipt and publish one immutable host record."""

    receipt = _read_object(receipt_path)
    solution = workspace / solution_relative_path
    submission = workspace / submission_relative_path
    if (
        not solution.is_file()
        or solution.is_symlink()
        or not submission.is_file()
        or submission.is_symlink()
    ):
        raise AdapterError("candidate files are missing or unsafe while publishing MLE state")
    if receipt.get("competition_id") != competition_id:
        raise AdapterError("MLE receipt belongs to a different competition")
    if receipt.get("evaluation_status") != "verified":
        raise AdapterError("MLE receipt is not verified")
    if receipt.get("format_validated") is not True:
        raise AdapterError("MLE receipt did not attest submission format")
    solution_hash = sha256_file(solution)
    submission_hash = sha256_file(submission)
    if receipt.get("solution_sha256") != solution_hash:
        raise AdapterError("solution.py changed after the MLE receipt was recorded")
    if receipt.get("submission_sha256") != submission_hash:
        raise AdapterError("submission.csv changed after the MLE receipt was recorded")
    try:
        raw_metric = float(receipt.get("raw_metric_numeric", receipt["metric"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise AdapterError("MLE receipt local metric is invalid") from exc
    if not math.isfinite(raw_metric):
        raise AdapterError("MLE receipt local metric must be finite")
    direction = str(receipt.get("metric_direction", ""))
    if direction not in {"maximize", "minimize"}:
        raise AdapterError("MLE receipt metric direction is invalid")
    raw_metric_text = str(receipt.get("raw_metric_text", receipt.get("metric", ""))).strip()
    if not raw_metric_text:
        raise AdapterError("MLE receipt raw metric text is missing")

    record = {
        "schema_version": STATE_SCHEMA_VERSION,
        "run_id": _component(run_id, label="run id"),
        "competition_id": _component(competition_id, label="competition id"),
        "node_id": _component(node_id, label="node id"),
        "attempt_id": _component(attempt_id, label="attempt id"),
        "solution_sha256": solution_hash,
        "submission_sha256": submission_hash,
        "raw_metric_text": raw_metric_text,
        "raw_metric_numeric": raw_metric,
        "metric_direction": direction,
        "evaluation_role": "local_only",
        "official_grading": False,
        "source_node_id": _component(node_id, label="node id"),
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    path = candidate_record_path(
        root,
        run_id=run_id,
        competition_id=competition_id,
        node_id=node_id,
        attempt_id=attempt_id,
    )
    _write_exclusive(path, record)
    return record


def find_bound_record(
    *,
    root: Path,
    run_id: str,
    competition_id: str,
    solution: Path,
    submission: Path,
    node_id: str | None = None,
) -> dict[str, Any]:
    """Return the unique local-only record bound to the current artifact pair."""

    if not solution.is_file() or solution.is_symlink():
        raise AdapterError("solution.py is missing or unsafe during MLE verification")
    if not submission.is_file() or submission.is_symlink():
        raise AdapterError("submission.csv is missing or unsafe during MLE verification")
    solution_hash = sha256_file(solution)
    submission_hash = sha256_file(submission)
    records_dir = (
        root
        / _component(run_id, label="run id")
        / _component(competition_id, label="competition id")
        / "candidates"
    )
    if not records_dir.is_dir():
        raise AdapterError("no host-owned MLE candidate records exist for this run")
    matches: list[dict[str, Any]] = []
    node_filter = _component(node_id, label="node id") if node_id is not None else None
    for path in sorted(records_dir.glob("*/*.json")):
        record = _read_object(path)
        if (
            record.get("schema_version") == STATE_SCHEMA_VERSION
            and record.get("run_id") == run_id
            and record.get("competition_id") == competition_id
            and record.get("solution_sha256") == solution_hash
            and record.get("submission_sha256") == submission_hash
            and record.get("evaluation_role") == "local_only"
            and record.get("official_grading") is False
            and (node_filter is None or record.get("node_id") == node_filter)
        ):
            matches.append(record)
    if not matches:
        raise AdapterError("no host-owned MLE record matches the current solution and submission")
    # Multiple Arbor nodes may independently produce byte-identical artifacts.
    # That does not make the artifact ambiguous: no Adapter ranking occurs and
    # any matching record proves the same code/submission pair was recorded.
    return matches[0]


__all__ = [
    "STATE_SCHEMA_VERSION",
    "candidate_record_path",
    "find_bound_record",
    "publish_receipt",
    "run_id_from_environment",
    "state_root_from_environment",
]
