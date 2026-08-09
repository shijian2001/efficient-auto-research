from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from BenchmarkAdapters.contracts import AdapterError
from BenchmarkAdapters.FMLBench.adapter import FMLRunRequest
from BenchmarkAdapters.FMLBench.aggregate import aggregate_fml
from BenchmarkAdapters.FMLBench.protocol import FMLProtocol
from BenchmarkAdapters.FMLBench.runner import run_fml_task
from BenchmarkAdapters.formal_contract import ModelTrackConfig, repetition_summary
from BenchmarkAdapters.formal_preflight import collect_formal_preflight
from BenchmarkAdapters.protocol import canonical_json, sha256_file, write_json_exclusive
from BenchmarkAdapters.registry import AGENTS, ROOT


def _git(*argv: str, cwd: Path) -> str:
    completed = subprocess.run(
        ["git", *argv], cwd=cwd, capture_output=True, text=True, check=True
    )
    return completed.stdout.strip()


def _model_config() -> ModelTrackConfig:
    return ModelTrackConfig(
        schema_version=1,
        model_track_id="synthetic-same-model-track",
        outer_model_id="synthetic-model-id",
        relay_base_url="http://relay.invalid/v1",
        model_parameters={
            "temperature": 0.2,
            "reasoning_effort": "medium",
            "max_output_tokens": 128,
        },
        request_timeout_seconds=30,
        retry_policy={"max_attempts": 1},
    )


def _fake_hardware() -> dict[str, object]:
    return {
        "gpu_type": "NVIDIA H100",
        "gpu_ids": ["0"],
        "gpus": [
            {
                "gpu_id": "0",
                "gpu_name": "NVIDIA H100 80GB HBM3",
                "gpu_uuid": "GPU-SYNTHETIC",
                "gpu_memory_total_mb": 81559,
            }
        ],
        "gpu_count": 1,
        "gpus_per_evaluation": 1,
        "max_concurrent_evaluations": 1,
        "gpu_exclusivity": "verified-and-host-locked",
    }


def _protocol(tmp_path: Path, *, outer_repetitions: int = 1) -> FMLProtocol:
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    _git("init", "-q", cwd=upstream)
    _git("config", "user.email", "synthetic@example.invalid", cwd=upstream)
    _git("config", "user.name", "Synthetic Test", cwd=upstream)
    runner = upstream / "fake_runner.py"
    runner.write_text(
        """from __future__ import annotations
import json
import os
import sys
from pathlib import Path

output = Path(sys.argv[1])
task = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
output.mkdir(parents=True, exist_ok=True)
(output / "artifact.bin").write_bytes(("artifact:" + task["task_id"]).encode())
(output / "result.json").write_text(json.dumps({
    "score": task["score"],
    "internal_rounds_completed": 2,
    "internal_proposals_completed": 4,
}), encoding="utf-8")
print("Authorization: Bearer " + os.environ.get("SYNTHETIC_API_KEY", "missing"))
print(os.environ.get("SYNTHETIC_API_KEY", "missing"))
""",
        encoding="utf-8",
    )
    task_paths = []
    for index, score in enumerate((0.25, 0.75)):
        path = upstream / f"task-{index}.json"
        path.write_text(
            json.dumps({"task_id": f"task-{index}", "score": score}), encoding="utf-8"
        )
        task_paths.append(path)
    _git("add", ".", cwd=upstream)
    _git("commit", "-qm", "synthetic frozen FML fixture", cwd=upstream)
    commit = _git("rev-parse", "HEAD", cwd=upstream)
    command = (
        sys.executable,
        str(runner),
        "{output_dir}",
        "{task_config}",
    )
    protocol = FMLProtocol(
        schema_version=1,
        benchmark_id="fml-bench",
        protocol_version="synthetic-fml-v1",
        upstream_root=upstream,
        upstream_commit=commit,
        task_config_paths=tuple(task_paths),
        task_config_digests={path.name: sha256_file(path) for path in task_paths},
        evaluator_files={runner.name: sha256_file(runner)},
        internal_round_policy="frozen-upstream-two-round-policy",
        internal_proposal_policy="frozen-upstream-four-proposal-policy",
        wall_clock_seconds=60,
        outer_run_ids=tuple(range(outer_repetitions)),
        gpu_type="NVIDIA H100",
        gpus_per_evaluation=1,
        max_concurrent_evaluations=1,
        launcher_commands={agent: command for agent in AGENTS},
        allowed_write_paths=("artifact.bin", "result.json"),
        metric_direction="maximize",
        artifact_relative_path="artifact.bin",
        upstream_result_relative_path="result.json",
        primary_metric_name="score",
    )
    protocol.validate(formal=True)
    return protocol


def _run_campaign(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    outer_repetitions: int,
    formal: bool = True,
) -> tuple[FMLProtocol, Path]:
    protocol = _protocol(tmp_path, outer_repetitions=outer_repetitions)
    campaign = tmp_path / "campaign"
    monkeypatch.setattr(
        "BenchmarkAdapters.FMLBench.runner._git_commit", lambda _path: ("a" * 40, False)
    )
    monkeypatch.setenv("SYNTHETIC_API_KEY", "never-serialize-this-secret")
    for outer_run_index in range(outer_repetitions):
        for task_config in protocol.task_config_paths:
            request = FMLRunRequest(
                agent="codex",
                protocol=protocol,
                model_config=_model_config(),
                task_config=task_config,
                output_dir=(
                    campaign
                    / "codex"
                    / f"run-{outer_run_index}"
                    / task_config.stem
                ),
                outer_run_index=outer_run_index,
                agent_variant="synthetic-codex-contract",
                formal=formal,
                credential_env_names=("SYNTHETIC_API_KEY",),
                gpu_ids=("0",),
            )
            record = run_fml_task(request, _hardware=_fake_hardware())
            assert record.score_valid is True
    return protocol, campaign


def test_repetition_summary_supports_one_and_three_runs() -> None:
    single = repetition_summary([0.5], outer_repetitions=1)
    assert single["reporting_label"] == "single_run"
    assert single["standard_deviation"] is None
    triple = repetition_summary([0.25, 0.5, 0.75], outer_repetitions=3)
    assert triple["reporting_label"] == "avg_at_3"
    assert triple["standard_deviation"] is not None
    with pytest.raises(AdapterError, match="requires all 3"):
        repetition_summary([0.25, 0.5], outer_repetitions=3)


@pytest.mark.parametrize(
    ("outer_repetitions", "expected_label"), ((1, "single_run"), (3, "avg_at_3"))
)
def test_fml_producer_manifest_evaluator_record_aggregate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    outer_repetitions: int,
    expected_label: str,
) -> None:
    protocol, campaign = _run_campaign(
        tmp_path, monkeypatch, outer_repetitions=outer_repetitions
    )
    aggregate = aggregate_fml(protocol=protocol, campaign_dir=campaign, agent="codex")
    assert aggregate["reporting_label"] == expected_label
    assert aggregate["metrics"]["mean_task_score"]["mean"] == pytest.approx(0.5)
    assert aggregate["tasks_per_outer_run"] == 2
    if outer_repetitions == 1:
        assert aggregate["formal_avg_at_3_valid"] is False
        assert aggregate["metrics"]["mean_task_score"]["standard_deviation"] is None
    else:
        assert aggregate["formal_avg_at_3_valid"] is True
    serialized_records = "\n".join(
        path.read_text(encoding="utf-8")
        for path in campaign.rglob("*.json")
    ).lower()
    assert "never-serialize-this-secret" not in serialized_records
    assert "authorization:" not in serialized_records
    assert "synthetic_api_key" not in serialized_records


@pytest.mark.parametrize("target", ("artifact", "evaluation", "manifest", "record"))
def test_fml_aggregate_rejects_tampering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    protocol, campaign = _run_campaign(tmp_path, monkeypatch, outer_repetitions=1)
    run_dir = campaign / "codex/run-0/task-0"
    if target == "artifact":
        (run_dir / "artifact.bin").write_bytes(b"tampered")
    elif target == "evaluation":
        (run_dir / "result.json").write_text('{"score": 999}', encoding="utf-8")
    elif target == "manifest":
        payload = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
        payload["agent_variant"] = "tampered"
        (run_dir / "manifest.json").write_text(json.dumps(payload), encoding="utf-8")
    else:
        payload = json.loads((run_dir / "task-record.json").read_text(encoding="utf-8"))
        payload["raw_score"] = 999
        (run_dir / "task-record.json").write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(AdapterError):
        aggregate_fml(protocol=protocol, campaign_dir=campaign, agent="codex")


def test_fml_rejects_missing_task_and_outer_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    protocol, campaign = _run_campaign(tmp_path, monkeypatch, outer_repetitions=3)
    missing = campaign / "codex/run-2/task-1/task-record.json"
    missing.unlink()
    with pytest.raises(AdapterError, match="missing task record"):
        aggregate_fml(protocol=protocol, campaign_dir=campaign, agent="codex")


def test_fml_smoke_record_cannot_enter_formal_aggregate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    protocol, campaign = _run_campaign(
        tmp_path, monkeypatch, outer_repetitions=1, formal=False
    )
    manifest = json.loads(
        (campaign / "codex/run-0/task-0/manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["non_formal"] is True
    assert manifest["non_comparable"] is True
    with pytest.raises(AdapterError, match="formal task record"):
        aggregate_fml(protocol=protocol, campaign_dir=campaign, agent="codex")


def test_model_placeholders_and_embedded_credentials_fail_closed() -> None:
    placeholder = replace(_model_config(), outer_model_id="<MODEL_ID>")
    placeholder.validate(formal=False)
    with pytest.raises(AdapterError, match="placeholders"):
        placeholder.validate(formal=True)
    embedded = replace(
        _model_config(), relay_base_url="https://user:password@relay.invalid/v1"
    )
    with pytest.raises(AdapterError, match="embed credentials"):
        embedded.validate(formal=True)


def test_fml_missing_concrete_command_fails_formal_preflight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    protocol = _protocol(tmp_path)
    protocol_path = tmp_path / "protocol.json"
    protocol.write(protocol_path)
    payload = json.loads(protocol_path.read_text(encoding="utf-8"))
    payload["launcher_commands"].pop("codex")
    payload_without_digest = {
        key: value for key, value in payload.items() if key != "protocol_digest"
    }
    payload["protocol_digest"] = __import__("hashlib").sha256(
        canonical_json(payload_without_digest)
    ).hexdigest()
    protocol_path.write_text(json.dumps(payload), encoding="utf-8")
    model_path = tmp_path / "model.json"
    write_json_exclusive(model_path, _model_config().to_dict())
    monkeypatch.setattr(
        "BenchmarkAdapters.formal_preflight.git_identity",
        lambda path: ("a" * 40, False),
    )
    report = collect_formal_preflight(
        benchmark_id="fml-bench",
        agent_id="codex",
        agent_variant="synthetic",
        protocol_path=protocol_path,
        model_config_path=model_path,
        formal=True,
    )
    checks = {check.name: check for check in report.checks}
    assert checks["launcher_config_complete"].passed is False
    with pytest.raises(AdapterError, match="formal preflight failed"):
        report.require_ready()


def test_fml_dirty_source_fails_formal_protocol(tmp_path: Path) -> None:
    protocol = _protocol(tmp_path)
    (protocol.upstream_root / "unreviewed.txt").write_text("dirty", encoding="utf-8")
    with pytest.raises(AdapterError, match="clean pinned upstream"):
        protocol.validate(formal=True)


def test_fml_formal_preflight_reports_dirty_adapter_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    protocol = _protocol(tmp_path)
    protocol_path = tmp_path / "protocol.json"
    protocol.write(protocol_path)
    model_path = tmp_path / "model.json"
    write_json_exclusive(model_path, _model_config().to_dict())

    def identity(path: Path) -> tuple[str, bool]:
        return "a" * 40, path.resolve() != protocol.upstream_root.resolve()

    monkeypatch.setattr("BenchmarkAdapters.formal_preflight.git_identity", identity)
    report = collect_formal_preflight(
        benchmark_id="fml-bench",
        agent_id="codex",
        agent_variant="synthetic",
        protocol_path=protocol_path,
        model_config_path=model_path,
        formal=True,
    )
    checks = {check.name: check for check in report.checks}
    assert checks["formal_source_clean"].passed is False
    assert report.passed is False
