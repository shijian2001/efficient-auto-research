"""Formal MLE run closeout: native Agent, final artifact, official grade."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from ..artifacts import PublishedArtifact, publish_artifact
from ..contracts import AdapterError
from ..protocol import BenchmarkMode
from ..protocol import sha256_file
from ..records import BenchmarkRunResult, RunManifest, RunStatus
from .adapter import MleLiteAdapter, MleLiteRequest
from .grading import OfficialGrade, grade_submission
from .membership import require_lite_task
from ..TerminalAO.supervisor import summarize_token_log


@dataclass(frozen=True)
class FormalMleOutcome:
    artifact: PublishedArtifact
    grade: OfficialGrade
    result: BenchmarkRunResult


def collect_token_usage(output_dir: Path) -> dict[str, int | None]:
    """Roll the relay's per-call telemetry into this cell's result record.

    Without this the campaign scores a cell but records `tokens: {}`, so
    cost-per-point -- the headline efficiency comparison across the seven Agents
    -- cannot be computed even though every number already sits on disk.

    Two adapter paths write the log in different places: the workspace and native
    host launchers get one `token_usage.jsonl` from the per-run relay, while the
    docker launcher writes a `relay-telemetry/<agent>_<task>_gpu<n>.jsonl` per
    container. Sum whatever is there so all seven Agents report on one basis.
    """
    output_dir = output_dir.resolve()
    logs = [output_dir / "token_usage.jsonl"]
    logs.extend(sorted((output_dir / "relay-telemetry").glob("*.jsonl")))
    present = [path for path in logs if path.is_file()]
    if not present:
        return {}
    combined: dict[str, int | None] = {}
    for path in present:
        for name, value in summarize_token_log(path).items():
            if value is None:
                # One unknown makes the whole total unknown: silently treating it
                # as zero would understate cost rather than admit it is missing.
                combined[name] = None
            elif combined.get(name, 0) is not None:
                combined[name] = int(combined.get(name, 0) or 0) + int(value)
    return combined


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
    report_digest = sha256_file(grade.report_path)
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
        metrics={**report, "grader_report_file_sha256": report_digest},
        artifact_path=str(artifact.path),
        artifact_sha256=artifact.sha256,
        wall_clock_seconds=time.monotonic() - started,
        tokens=collect_token_usage(request.output_dir),
    )
    result.write(run_dir / "result.json")
    return FormalMleOutcome(artifact, grade, result)


__all__ = ["FormalMleOutcome", "run_formal_mle"]
