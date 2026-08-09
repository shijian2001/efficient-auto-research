"""Generate the frozen two-seed Optimizer Design baseline score record."""

from __future__ import annotations

import hashlib
from dataclasses import asdict
from pathlib import Path

from ..contracts import AdapterError
from ..protocol import canonical_json, sha256_file, write_json_exclusive
from .evaluator import OptimizerDesignEvaluator, score_validation_trajectories
from .protocol import BaselineScoreRecord, OptimizerDesignProtocol, SourceManifest
from .resource import optimizer_design_resource_lease


def run_optimizer_design_baseline(
    *,
    protocol: OptimizerDesignProtocol,
    source_root: Path,
    data_root: Path,
    environment_python: Path,
    gpu_ids: tuple[str, ...],
    cpu_set: str,
    memory_limit_gib: int,
    output_dir: Path,
) -> BaselineScoreRecord:
    protocol.validate(source_root, data_root, environment_python)
    protocol.require_formal_contract()
    output_dir = output_dir.resolve()
    if output_dir.exists() or output_dir.is_symlink():
        raise AdapterError(f"Optimizer Design baseline output already exists: {output_dir}")
    source_manifest = SourceManifest.load(protocol.source_manifest_path)
    baseline_path = source_root.resolve() / source_manifest.editable_path
    with optimizer_design_resource_lease(
        protocol=protocol,
        gpu_ids=gpu_ids,
        environment_python=environment_python,
        cpu_set=cpu_set,
        memory_limit_gib=memory_limit_gib,
    ) as hardware:
        output_dir.mkdir(parents=True)
        evidence_root = output_dir / "baseline-evidence"
        evaluator = OptimizerDesignEvaluator(
            protocol=protocol,
            source_root=source_root,
            data_root=data_root,
            environment_python=environment_python,
            gpu_ids=gpu_ids,
            timeout_seconds=protocol.candidate_timeout_seconds,
            sandbox=True,
            validate_assets=False,
        )
        evaluations = tuple(
            evaluator.evaluate(
                baseline_path,
                seed=seed,
                output_dir=evidence_root / f"held-out-{index}",
                evaluation_id=f"baseline-held-out-{index}",
            )
            for index, seed in enumerate(protocol.held_out_seeds, 1)
        )
    if not all(item.score_valid and item.score_steps is not None for item in evaluations):
        raise AdapterError("Optimizer Design baseline evaluation failed; record remains pending")
    final_score, mean_loss = score_validation_trajectories(
        tuple(item.validation_trajectory for item in evaluations),
        target=protocol.target_val_loss,
        significance_margin=protocol.significance_margin,
        penalty=protocol.failure_penalty_steps,
    )
    write_json_exclusive(evidence_root / "hardware.json", hardware)
    evidence_files = {
        path.relative_to(output_dir).as_posix(): sha256_file(path)
        for path in sorted(evidence_root.rglob("*"))
        if path.is_file() and not path.is_symlink()
    }
    record = BaselineScoreRecord(
        schema_version=1,
        protocol_id=protocol.protocol_id,
        status="completed",
        baseline_candidate_sha256=source_manifest.baseline_candidate_sha256,
        source_manifest_digest=protocol.source_manifest_digest,
        data_manifest_digest=protocol.data_manifest_digest,
        environment_manifest_digest=protocol.environment_manifest_digest,
        evaluator_manifest_digest=protocol.evaluator_manifest_digest,
        agent_runtime_manifest_digest=protocol.agent_runtime_manifest_digest,
        environment_lock_digest=protocol.environment_lock_digest,
        held_out_seeds=protocol.held_out_seeds,
        held_out_score_steps={
            str(seed): int(evaluation.score_steps)
            for seed, evaluation in zip(protocol.held_out_seeds, evaluations)
        },
        final_score_steps=float(final_score),
        evaluation_record_digests={
            str(seed): evaluation.digest
            for seed, evaluation in zip(protocol.held_out_seeds, evaluations)
        },
        hardware_fingerprint=hashlib.sha256(canonical_json(hardware)).hexdigest(),
        evidence_files=evidence_files,
        note=(
            "Completed on the frozen two non-cherry-picked held-out seeds using the protected "
            f"four-H100 evaluator; earliest common significant mean loss={mean_loss!r}."
        ),
    )
    record.validate(output_dir)
    write_json_exclusive(
        output_dir / "completed-baseline-score-record.json",
        {**record.to_dict(), "record_digest": record.digest},
    )
    write_json_exclusive(
        output_dir / "baseline-summary.json",
        {
            "protocol_id": protocol.protocol_id,
            "protocol_digest": protocol.digest,
            "final_score_steps": final_score,
            "common_mean_val_loss": mean_loss,
            "evaluations": [asdict(item) for item in evaluations],
            "hardware": hardware,
            "completed_record_digest": record.digest,
        },
    )
    return record


__all__ = ["run_optimizer_design_baseline"]
