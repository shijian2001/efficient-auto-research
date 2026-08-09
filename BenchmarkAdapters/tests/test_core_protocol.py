from __future__ import annotations

import json
from pathlib import Path

import pytest

import benchmark_adapters
import BenchmarkAdapters
from BenchmarkAdapters.artifacts import publish_artifact
from BenchmarkAdapters.contracts import AdapterError
from BenchmarkAdapters.formal_contract import hardware_comparison_fingerprint
from BenchmarkAdapters.preflight import _evidence_for
from BenchmarkAdapters.process import redact_sensitive_payload
from BenchmarkAdapters.protocol import BenchmarkMode, FormalProtocol
from BenchmarkAdapters.readiness import ReadinessEvidence, ReadinessLevel
from BenchmarkAdapters.records import BenchmarkRunResult, RunManifest, RunStatus


DIGEST = "a" * 64


def _protocol(**updates) -> FormalProtocol:
    values = {
        "protocol_id": "mle-lite-v1",
        "mode": BenchmarkMode.MLE,
        "task_ids": ("task-a", "task-b"),
        "asset_digests": {"dataset": DIGEST},
        "model": "gpt-5.5",
        "reasoning_effort": "high",
        "temperature": 1.0,
        "wall_clock_seconds": 3600,
        "seeds": (0, 1, 2),
        "retry_policy": "relay-20",
        "failure_policy": "failures-remain-in-denominator",
        "artifact_policy": "single-hash-bound-final",
        "aggregation_policy": "per-seed-rates-then-mean",
        "hardware_policy": "one-gpu-fixed-cpu",
    }
    values.update(updates)
    return FormalProtocol(**values)


def _manifest(protocol: FormalProtocol, **updates) -> RunManifest:
    values = {
        "run_id": "run-1",
        "protocol_id": protocol.protocol_id,
        "protocol_digest": protocol.digest,
        "mode": protocol.mode,
        "agent": "codex",
        "agent_commit": "abcdef1234567",
        "adapter_commit": "7654321abcdef",
        "source_dirty": False,
        "task_id": "task-a",
        "seed": 0,
        "model": protocol.model,
        "reasoning_effort": protocol.reasoning_effort,
        "temperature": protocol.temperature,
        "wall_clock_seconds": protocol.wall_clock_seconds,
        "asset_digests": protocol.asset_digests,
        "hardware": {"gpu": "GPU-test", "cpus": "0-20"},
        "policies": {"failure": protocol.failure_policy},
    }
    values.update(updates)
    return RunManifest(**values)


def test_compatibility_package_exports_canonical_objects() -> None:
    assert benchmark_adapters.FormalProtocol is BenchmarkAdapters.FormalProtocol
    assert benchmark_adapters.RunManifest is BenchmarkAdapters.RunManifest
    assert benchmark_adapters.publish_artifact is BenchmarkAdapters.publish_artifact
    assert tuple(mode.value for mode in BenchmarkAdapters.BenchmarkMode) == (
        "mle",
        "autoresearch",
        "optimizer-design",
        "terminal-ao",
        "terminal-direct-smoke",
        "fml-bench",
    )


def test_protocol_digest_is_stable_and_file_is_immutable(tmp_path: Path) -> None:
    protocol = _protocol()
    first = protocol.digest
    assert first == protocol.digest
    path = tmp_path / "protocol.json"
    protocol.write(path)
    assert FormalProtocol.load(path).digest == first
    with pytest.raises(AdapterError, match="refusing to overwrite"):
        protocol.write(path)


def test_formal_protocol_rejects_missing_digest_and_direct_mode() -> None:
    with pytest.raises(AdapterError, match="asset digests"):
        _protocol(asset_digests={}).validate()
    with pytest.raises(AdapterError, match="smoke mode"):
        _protocol(mode=BenchmarkMode.TERMINAL_DIRECT_SMOKE).validate()


def test_manifest_rejects_dirty_formal_source_and_overwrite(tmp_path: Path) -> None:
    protocol = _protocol()
    with pytest.raises(AdapterError, match="clean source"):
        _manifest(protocol, source_dirty=True).validate()
    manifest = _manifest(protocol)
    path = tmp_path / "manifest.json"
    manifest.write(path)
    payload = json.loads(path.read_text())
    assert payload["manifest_digest"] == manifest.digest
    with pytest.raises(AdapterError, match="refusing to overwrite"):
        manifest.write(path)


def test_published_artifact_is_hash_bound_and_not_overwritten(tmp_path: Path) -> None:
    source = tmp_path / "candidate.csv"
    source.write_text("id,target\n1,1\n", encoding="utf-8")
    destination = tmp_path / "run/artifacts/final/submission.csv"
    artifact = publish_artifact(source, destination)
    assert artifact.path.read_bytes() == source.read_bytes()
    assert len(artifact.sha256) == 64
    with pytest.raises(AdapterError, match="refusing to overwrite"):
        publish_artifact(source, destination)


def test_result_requires_hashed_artifact_for_valid_score(tmp_path: Path) -> None:
    protocol = _protocol()
    manifest = _manifest(protocol)
    invalid = BenchmarkRunResult(
        run_id=manifest.run_id,
        protocol_id=protocol.protocol_id,
        protocol_digest=protocol.digest,
        manifest_digest=manifest.digest,
        mode=protocol.mode,
        agent=manifest.agent,
        task_id=manifest.task_id,
        seed=manifest.seed,
        status=RunStatus.COMPLETED,
        score_valid=True,
        score=0.5,
        metrics={},
        artifact_path=None,
        artifact_sha256=None,
        wall_clock_seconds=1.0,
    )
    with pytest.raises(AdapterError, match="hashed final artifact"):
        invalid.validate()


def test_smoke_readiness_requires_durable_evidence(tmp_path: Path) -> None:
    evidence = ReadinessEvidence(
        agent="codex",
        mode="mle",
        level=ReadinessLevel.REAL_SMOKE_READY,
        evidence_path=str(tmp_path / "missing.json"),
        observed_at="2026-08-06T00:00:00Z",
        detail="scored smoke",
    )
    with pytest.raises(AdapterError, match="durable evidence"):
        evidence.validate()
    evidence_file = tmp_path / "smoke.json"
    evidence_file.write_text("{}\n", encoding="utf-8")
    ready = ReadinessEvidence(
        **{**evidence.__dict__, "evidence_path": str(evidence_file)}
    )
    ready.validate()


def test_preflight_evidence_must_bind_scored_protocol_result(tmp_path: Path) -> None:
    evidence = tmp_path / "mle/codex/formal.json"
    evidence.parent.mkdir(parents=True)
    evidence.write_text("{}\n", encoding="utf-8")
    level, path = _evidence_for("codex", "mle", DIGEST, tmp_path)
    assert level is ReadinessLevel.COMMAND_READY
    assert path is None

    evidence.write_text(
        json.dumps(
            {
                "agent": "codex",
                "mode": "mle",
                "protocol_digest": DIGEST,
                "status": "completed",
                "score_valid": True,
                "score": 0.5,
                "artifact_sha256": "b" * 64,
            }
        ),
        encoding="utf-8",
    )
    level, path = _evidence_for("codex", "mle", DIGEST, tmp_path)
    assert level is ReadinessLevel.FORMAL_PROTOCOL_READY
    assert path == str(evidence)


def test_autoresearch_readiness_rejects_summary_only_evidence(tmp_path: Path) -> None:
    evidence = tmp_path / "autoresearch/ear/formal.json"
    evidence.parent.mkdir(parents=True)
    evidence.write_text(
        json.dumps(
            {
                "agent": "ear",
                "mode": "autoresearch",
                "protocol_digest": DIGEST,
                "status": "completed",
                "score_valid": True,
                "score": 1.0,
                "artifact_sha256": "b" * 64,
            }
        ),
        encoding="utf-8",
    )
    level, path = _evidence_for("ear", "autoresearch", DIGEST, tmp_path)
    assert level is ReadinessLevel.COMMAND_READY
    assert path is None


def test_hardware_comparison_ignores_physical_gpu_allocation_identity() -> None:
    first = {
        "gpu_type": "RTX 4090",
        "gpu_count": 1,
        "gpu_ids": ["0"],
        "gpus": [
            {
                "gpu_id": "0",
                "gpu_name": "NVIDIA GeForce RTX 4090",
                "gpu_uuid": "GPU-FIRST",
                "gpu_memory_total_mb": 24564,
            }
        ],
        "gpus_per_evaluation": 1,
        "max_concurrent_evaluations": 1,
        "gpu_exclusivity": "verified-and-host-locked",
    }
    second = {
        **first,
        "gpu_ids": ["7"],
        "gpus": [{**first["gpus"][0], "gpu_id": "7", "gpu_uuid": "GPU-SECOND"}],
    }
    assert hardware_comparison_fingerprint(first) == hardware_comparison_fingerprint(second)
    second["gpus"][0]["gpu_name"] = "NVIDIA H100 80GB HBM3"
    assert hardware_comparison_fingerprint(first) != hardware_comparison_fingerprint(second)


def test_sensitive_payload_redaction_preserves_token_telemetry() -> None:
    secret = "synthetic-never-log"
    payload = {
        "authorization": f"Bearer {secret}",
        "input_tokens": 3,
        "token_usage": {"output_tokens": 2},
        "message": f"Authorization: Bearer {secret}\n{secret}",
        "nested": {"api_key": secret},
    }
    redacted = redact_sensitive_payload(payload, {"OPENAI_API_KEY": secret})
    serialized = json.dumps(redacted).lower()
    assert redacted["input_tokens"] == 3
    assert redacted["token_usage"]["output_tokens"] == 2
    assert secret not in serialized
    assert "authorization" not in serialized
    assert "api_key" not in serialized
