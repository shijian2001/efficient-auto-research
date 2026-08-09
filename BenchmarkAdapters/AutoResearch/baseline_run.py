"""Generate a completed Autoresearch baseline candidate from real evaluator evidence."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Mapping

from ..contracts import AdapterError
from ..protocol import canonical_json, sha256_file, write_json_exclusive
from .baseline import BaselineManifest
from .evaluator import CandidateEvaluator
from .protocol import AutoResearchProtocol, BaselineScoreRecord
from .revisions import TrainRevisionStore
from .seed_injection import SeedPolicy


def run_autoresearch_baseline(
    *,
    protocol: AutoResearchProtocol,
    prepared_root: Path,
    evaluator: CandidateEvaluator,
    hardware: Mapping[str, object],
    output_dir: Path,
) -> BaselineScoreRecord:
    protocol.validate(prepared_root, evaluator.kernel_cache_root)
    protocol.require_formal_contract()
    output_dir = output_dir.resolve()
    if output_dir.exists() or output_dir.is_symlink():
        raise AdapterError(f"Autoresearch baseline output already exists: {output_dir}")
    baseline = BaselineManifest.load(protocol.baseline_manifest_path)
    seed_policy = SeedPolicy.load(protocol.seed_policy_path)
    if evaluator.protocol_digest != protocol.digest:
        raise AdapterError("Autoresearch baseline evaluator is not bound to the protocol digest")
    if evaluator.benchmark_commit != baseline.source_commit:
        raise AdapterError("Autoresearch baseline evaluator is not bound to the benchmark commit")
    if not evaluator.sandbox or not evaluator.attest_evaluate_bpb:
        raise AdapterError("Autoresearch baseline requires the protected sandbox evaluator")
    if "H100" not in str(hardware.get("gpu_name", "")) or not hardware.get("gpu_uuid"):
        raise AdapterError("Autoresearch baseline requires an attested H100 GPU")
    output_dir.mkdir(parents=True)
    evidence_root = output_dir / "baseline-evidence"
    store = TrainRevisionStore(
        baseline_source=protocol.source_root,
        baseline_manifest=baseline,
        state_dir=output_dir / "baseline-revision-state",
    )
    evaluations = tuple(
        evaluator.evaluate(
            store=store,
            revision_id="baseline",
            seed=seed,
            output_dir=evidence_root / f"held-out-{index}",
            evaluation_id=f"baseline-held-out-{index}",
            agent="host-baseline",
            candidate_sequence=index,
        )
        for index, seed in enumerate(seed_policy.held_out_seeds, 1)
    )
    if not all(item.score_valid and item.val_bpb is not None for item in evaluations):
        raise AdapterError("Autoresearch baseline evaluation failed; tracked record remains pending")
    write_json_exclusive(evidence_root / "hardware.json", dict(hardware))
    evidence_files = {
        path.relative_to(output_dir).as_posix(): sha256_file(path)
        for path in sorted(evidence_root.rglob("*"))
        if path.is_file() and not path.is_symlink()
    }
    held_out_scores = {
        str(seed): float(evaluation.val_bpb)
        for seed, evaluation in zip(seed_policy.held_out_seeds, evaluations)
    }
    record = BaselineScoreRecord(
        schema_version=2,
        protocol_id=protocol.protocol_id,
        status="completed",
        baseline_train_sha256=baseline.baseline_train_sha256,
        baseline_manifest_digest=protocol.baseline_manifest_digest,
        prepared_manifest_digest=protocol.prepared_manifest_digest,
        kernel_cache_manifest_digest=protocol.kernel_cache_manifest_digest,
        evaluator_manifest_digest=protocol.evaluator_manifest_digest,
        seed_policy_digest=protocol.seed_policy_digest,
        primary_metric="val_bpb",
        metric_direction="minimize",
        held_out_seeds=seed_policy.held_out_seeds,
        held_out_scores=held_out_scores,
        final_score=sum(held_out_scores.values()) / len(held_out_scores),
        evaluation_record_digests={
            str(seed): evaluation.digest
            for seed, evaluation in zip(seed_policy.held_out_seeds, evaluations)
        },
        note=(
            "Completed by the protected evaluator on both frozen held-out seeds using an "
            "exclusive H100 allocation; requires explicit reviewed promotion."
        ),
        benchmark_commit=baseline.source_commit,
        hardware_fingerprint=hashlib.sha256(canonical_json(dict(hardware))).hexdigest(),
        evidence_files=evidence_files,
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
            "protocol_digest_at_evaluation": protocol.digest,
            "held_out_scores": held_out_scores,
            "final_score": record.final_score,
            "hardware_fingerprint": record.hardware_fingerprint,
            "completed_record_digest": record.digest,
            "promotion_required": True,
        },
    )
    return record


__all__ = ["run_autoresearch_baseline"]
