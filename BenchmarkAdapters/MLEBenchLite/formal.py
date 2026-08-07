"""Formal MLE run closeout: native Agent, final artifact, official grade."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from ..artifacts import PublishedArtifact, publish_artifact
from ..contracts import AdapterError
from ..protocol import BenchmarkMode
from ..records import BenchmarkRunResult, RunManifest, RunStatus
from .adapter import MleLiteAdapter, MleLiteRequest
from .grading import OfficialGrade, grade_submission
from .membership import require_lite_task


@dataclass(frozen=True)
class FormalMleOutcome:
    artifact: PublishedArtifact
    grade: OfficialGrade
    result: BenchmarkRunResult


def run_formal_mle(
    *,
    request: MleLiteRequest,
    manifest: RunManifest,
    run_dir: Path,
    log_path: Path | None = None,
) -> FormalMleOutcome:
    require_lite_task(request.competition_id)
    if manifest.mode is not BenchmarkMode.MLE:
        raise AdapterError("formal MLE run requires an MLE manifest")
    if manifest.agent != request.agent or manifest.task_id != request.competition_id:
        raise AdapterError("MLE request does not match manifest identity")
    manifest.validate()
    run_dir = run_dir.resolve()
    manifest_path = run_dir / "manifest.json"
    manifest.write(manifest_path)
    started = time.monotonic()
    candidate = MleLiteAdapter(request.agent).run(request, log_path=log_path)
    artifact = publish_artifact(candidate, run_dir / "artifacts/final/submission.csv")
    grade = grade_submission(
        competition_id=request.competition_id,
        submission=artifact.path,
        data_root=request.data_root,
        report_path=run_dir / "grading/competition_report.json",
    )
    report = grade.report
    result = BenchmarkRunResult(
        run_id=manifest.run_id,
        protocol_id=manifest.protocol_id,
        protocol_digest=manifest.protocol_digest,
        manifest_digest=manifest.digest,
        mode=BenchmarkMode.MLE,
        agent=manifest.agent,
        task_id=manifest.task_id,
        seed=manifest.seed,
        status=RunStatus.COMPLETED,
        score_valid=True,
        score=float(report["score"]),
        metrics=report,
        artifact_path=str(artifact.path),
        artifact_sha256=artifact.sha256,
        wall_clock_seconds=time.monotonic() - started,
    )
    result.write(run_dir / "result.json")
    return FormalMleOutcome(artifact, grade, result)


__all__ = ["FormalMleOutcome", "run_formal_mle"]
