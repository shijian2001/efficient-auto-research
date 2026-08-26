"""Canonical host-owned MLE-Bench Lite grading contract."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..contracts import AdapterError, require_file
from ..protocol import sha256_file
from ..registry import ROOT
from .membership import require_lite_task


DEFAULT_MLE_PYTHON = ROOT / "mle-bench-lite/.venv/bin/python"
GRADER_WORKER = Path(__file__).with_name("grader_worker.py")
METRIC_DIRECTION_WORKER = Path(__file__).with_name("metric_direction_worker.py")


@dataclass(frozen=True)
class OfficialGrade:
    report_path: Path
    report: dict[str, Any]
    grader_worker_sha256: str

    @property
    def valid(self) -> bool:
        return bool(self.report.get("valid_submission")) and self.report.get("score") is not None


def grade_submission(
    *,
    competition_id: str,
    submission: Path,
    data_root: Path,
    report_path: Path,
    python_executable: Path = DEFAULT_MLE_PYTHON,
    enforce_lite_membership: bool = True,
) -> OfficialGrade:
    if enforce_lite_membership:
        require_lite_task(competition_id)
    submission = require_file(submission, "MLE submission")
    python_executable = python_executable.expanduser().absolute()
    if not python_executable.is_file() or not os.access(python_executable, os.X_OK):
        raise AdapterError(f"MLE-Bench Python does not exist: {python_executable}")
    report_path = report_path.resolve()
    if report_path.exists() or report_path.is_symlink():
        raise AdapterError(f"refusing to overwrite official grading report: {report_path}")
    completed = subprocess.run(
        [
            str(python_executable),
            str(GRADER_WORKER),
            "--data-root",
            str(data_root.resolve()),
            "--competition-id",
            competition_id,
            "--submission",
            str(submission),
            "--output",
            str(report_path),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=3600,
        check=False,
    )
    if not report_path.is_file():
        output = (completed.stdout or "") + (completed.stderr or "")
        raise AdapterError(
            f"official MLE grader produced no report (exit {completed.returncode}): "
            f"{output[-4000:]}"
        )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    expected_report_digest = report.pop("grader_report_digest", None)
    actual_report_digest = __import__("hashlib").sha256(
        __import__("BenchmarkAdapters.protocol", fromlist=["canonical_json"]).canonical_json(report)
    ).hexdigest()
    report["grader_report_digest"] = expected_report_digest
    if (
        expected_report_digest != actual_report_digest
        or report.get("submission_sha256") != sha256_file(submission)
        or report.get("competition_id") != competition_id
    ):
        raise AdapterError("official MLE grader report is not bound to the submitted artifact")
    grade = OfficialGrade(report_path, report, sha256_file(GRADER_WORKER))
    if completed.returncode != 0 or not grade.valid:
        raise AdapterError(
            f"official MLE grader rejected submission for {competition_id}: "
            f"valid={report.get('valid_submission')} score={report.get('score')}"
        )
    return grade


def metric_is_lower_better(
    *,
    competition_id: str,
    data_root: Path,
    python_executable: Path = DEFAULT_MLE_PYTHON,
    enforce_lite_membership: bool = True,
) -> bool:
    """Resolve the official metric direction from the frozen MLE-Bench leaderboard.

    The official ``mlebench`` package only exists inside the locked MLE-Bench
    environment, so the answer is produced by a host-owned worker instead of by
    any Agent-side environment.
    """

    if enforce_lite_membership:
        require_lite_task(competition_id)
    python_executable = python_executable.expanduser().absolute()
    if not python_executable.is_file() or not os.access(python_executable, os.X_OK):
        raise AdapterError(f"MLE-Bench Python does not exist: {python_executable}")
    completed = subprocess.run(
        [
            str(python_executable),
            str(METRIC_DIRECTION_WORKER),
            "--data-root",
            str(data_root.resolve()),
            "--competition-id",
            competition_id,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    if completed.returncode:
        raise AdapterError(
            f"cannot resolve the official metric direction for {competition_id}: "
            f"{(completed.stderr or completed.stdout)[-2000:]}"
        )
    try:
        payload = json.loads(completed.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        raise AdapterError(
            f"official metric-direction worker produced no usable report for {competition_id}"
        ) from exc
    if payload.get("competition_id") != competition_id or not isinstance(
        payload.get("is_lower_better"), bool
    ):
        raise AdapterError(
            f"official metric-direction report is not bound to {competition_id}"
        )
    return bool(payload["is_lower_better"])


__all__ = [
    "DEFAULT_MLE_PYTHON",
    "METRIC_DIRECTION_WORKER",
    "OfficialGrade",
    "grade_submission",
    "metric_is_lower_better",
]
