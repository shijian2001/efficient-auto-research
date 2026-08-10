from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import textwrap
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from BenchmarkAdapters.contracts import AdapterError
from BenchmarkAdapters.FMLBench.adapter import FMLRunRequest
from BenchmarkAdapters.FMLBench.agents import (
    FML_AGENT_ADAPTERS,
    FMLAgentAdapter,
    adapter_registry_digest,
    get_fml_agent_adapter,
)
from BenchmarkAdapters.FMLBench.aggregate import aggregate_fml
from BenchmarkAdapters.FMLBench.audit import rendered_instruction_audit
from BenchmarkAdapters.FMLBench.fake_relay import capture_relay
from BenchmarkAdapters.FMLBench.protocol import FMLProtocol, SHARED_ADAPTER_FILES
from BenchmarkAdapters.FMLBench.readiness import collect_fml_readiness
from BenchmarkAdapters.FMLBench.runner import run_fml_task
from BenchmarkAdapters.FMLBench.task import load_fml_task
from BenchmarkAdapters.formal_contract import ModelTrackConfig
from BenchmarkAdapters.formal_preflight import collect_formal_preflight
from BenchmarkAdapters.protocol import sha256_file, write_json_exclusive
from BenchmarkAdapters.registry import AGENTS, ROOT


def _git(*arguments: str, cwd: Path) -> str:
    completed = subprocess.run(
        ["git", *arguments], cwd=cwd, capture_output=True, text=True, check=True
    )
    return completed.stdout.strip()


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


def _model_config(base_url: str) -> ModelTrackConfig:
    return ModelTrackConfig(
        schema_version=1,
        model_track_id="synthetic-same-model-track",
        outer_model_id="synthetic-model-id",
        relay_base_url=base_url,
        model_parameters={
            "temperature": 0.2,
            "reasoning_effort": "medium",
            "max_output_tokens": 128,
        },
        request_timeout_seconds=30,
        retry_policy={"max_attempts": 1},
    )


def _write_fake_runtime(path: Path) -> Path:
    path.write_text(
        textwrap.dedent(
            """
            #!/usr/bin/env python3
            from __future__ import annotations

            import argparse
            import json
            import os
            import subprocess
            import urllib.request
            from pathlib import Path

            parser = argparse.ArgumentParser()
            parser.add_argument("--workspace", type=Path, required=True)
            parser.add_argument("--output-dir", type=Path, required=True)
            parser.add_argument("--task-input", required=True)
            parser.add_argument("--dev-command", required=True)
            parser.add_argument("--agent-id", required=True)
            args = parser.parse_args()
            request = urllib.request.Request(
                os.environ["OPENAI_BASE_URL"].rstrip("/") + "/chat/completions",
                data=json.dumps({
                    "model": os.environ["FML_MODEL_ID"],
                    "messages": [{"role": "user", "content": args.task_input}],
                }).encode(),
                headers={"Content-Type": "application/json", "Authorization": "Bearer proxy"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=10) as response:
                relay_payload = json.load(response)
            (args.workspace / "solution.py").write_text("VALUE = 2\\n", encoding="utf-8")
            dev = subprocess.run(
                args.dev_command,
                cwd=args.workspace,
                shell=True,
                capture_output=True,
                text=True,
                check=False,
            )
            if dev.returncode:
                raise SystemExit(dev.returncode)
            args.output_dir.mkdir(parents=True, exist_ok=True)
            (args.output_dir / "native-result.json").write_text(json.dumps({
                "native_agent": args.agent_id,
                "relay_model": relay_payload["model"],
                "development": json.loads(dev.stdout),
                "token_usage": {"input_tokens": 1, "output_tokens": 1},
                "request_count": 1,
            }), encoding="utf-8")
            print("Authorization: Bearer synthetic-secret")
            """
        ).lstrip(),
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _synthetic_upstream(tmp_path: Path) -> tuple[Path, Path]:
    upstream = tmp_path / "upstream"
    task_root = upstream / "ml_tasks/Synthetic_task"
    repository = upstream / "workspace/Synthetic_task/repo"
    config_root = upstream / "configs/tasks"
    task_root.joinpath("baseline_results").mkdir(parents=True)
    repository.mkdir(parents=True)
    config_root.mkdir(parents=True)
    evaluator = task_root / "evaluator.py"
    evaluator.write_text(
        textwrap.dedent(
            """
            import argparse
            import json
            from pathlib import Path

            parser = argparse.ArgumentParser()
            parser.add_argument("--phase", choices=("val", "test"), required=True)
            args = parser.parse_args()
            namespace = {}
            exec(Path("solution.py").read_text(encoding="utf-8"), namespace)
            output = Path("results_tmp") / ("val_info.json" if args.phase == "val" else "test_info.json")
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps({"synthetic": {"means": {"score": namespace["VALUE"]}}}), encoding="utf-8")
            """
        ),
        encoding="utf-8",
    )
    (task_root / "config.json").write_text(
        json.dumps(
            {
                "repo_dir": "workspace/Synthetic_task/repo",
                "pinned_commit": "synthetic",
                "conda_env": "synthetic-env",
                "target_files": ["solution.py"],
                "val_command": "python ../../../ml_tasks/Synthetic_task/evaluator.py --phase val",
                "test_command": "python ../../../ml_tasks/Synthetic_task/evaluator.py --phase test",
                "metric": "score",
                "metric_direction": "higher",
            }
        ),
        encoding="utf-8",
    )
    (task_root / "prompt.json").write_text(
        json.dumps(
            {
                "system": "Improve a tiny deterministic program.",
                "task_description": "Increase VALUE using only development feedback.",
            }
        ),
        encoding="utf-8",
    )
    baseline = {"synthetic": {"means": {"score": 1.0}}}
    for phase in ("val", "test"):
        (task_root / f"baseline_results/{phase}_info.json").write_text(
            json.dumps(baseline), encoding="utf-8"
        )
    (repository / "solution.py").write_text("VALUE = 1\n", encoding="utf-8")
    task_config = config_root / "synthetic.yaml"
    task_config.write_text(
        yaml.safe_dump(
            {
                "benchmark": {"name": "Synthetic_task"},
                "metrics": {
                    "include_datasets": ["synthetic"],
                    "include_metrics": ["score"],
                    "per_metric_direction": {"score": "higher"},
                },
            }
        ),
        encoding="utf-8",
    )
    (upstream / "benchmark").mkdir()
    for relative in (
        "benchmark/executor.py",
        "benchmark/runner.py",
        "benchmark/utils.py",
        "compute_agent_metrics.py",
    ):
        path = upstream / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# synthetic evaluator identity\n", encoding="utf-8")
    _git("init", "-q", cwd=upstream)
    _git("config", "user.email", "synthetic@example.invalid", cwd=upstream)
    _git("config", "user.name", "Synthetic Test", cwd=upstream)
    _git("add", ".", cwd=upstream)
    _git("commit", "-qm", "synthetic FML upstream", cwd=upstream)
    return upstream, task_config


def _protocol(
    tmp_path: Path,
    *,
    outer_repetitions: int = 1,
    primary_metric: str = "average_improvement",
    formal_status: str = "frozen",
    max_agent_steps: int = 2,
) -> FMLProtocol:
    upstream, task_config = _synthetic_upstream(tmp_path)
    evaluator_files = {
        relative: sha256_file(upstream / relative)
        for relative in (
            "benchmark/executor.py",
            "benchmark/runner.py",
            "benchmark/utils.py",
            "compute_agent_metrics.py",
        )
    }
    protocol = FMLProtocol(
        schema_version=2,
        benchmark_id="fml-bench",
        protocol_version="synthetic-fml-v2",
        upstream_root=upstream,
        upstream_commit=_git("rev-parse", "HEAD", cwd=upstream),
        task_config_paths=(task_config,),
        task_config_digests={task_config.name: sha256_file(task_config)},
        evaluator_files=evaluator_files,
        shared_adapter_files={
            relative: sha256_file(ROOT / relative) for relative in SHARED_ADAPTER_FILES
        },
        agent_adapter_digest=adapter_registry_digest(),
        internal_round_policy="one native Agent run per task",
        internal_proposal_policy="at most two development evaluations",
        wall_clock_seconds=60,
        outer_run_ids=tuple(range(outer_repetitions)),
        gpu_type="NVIDIA H100",
        gpus_per_evaluation=1,
        max_concurrent_evaluations=1,
        agent_adapter_ids=tuple(AGENTS),
        max_agent_steps=max_agent_steps,
        max_evaluator_calls=2,
        allowed_dependency_policy="frozen synthetic environment",
        task_score_ranges={"synthetic": {"best": 10.0, "worst": 0.0}},
        primary_metric_name=primary_metric,
        formal_status=formal_status,
    )
    protocol.validate(formal=formal_status == "frozen" and primary_metric in {"average_improvement", "win_rate"})
    return protocol


def _run_cell(
    *,
    protocol: FMLProtocol,
    model_config: ModelTrackConfig,
    fake_runtime: Path,
    campaign: Path,
    agent: str,
    outer_run_index: int,
    monkeypatch: pytest.MonkeyPatch,
    formal: bool = True,
):
    monkeypatch.setattr(
        "BenchmarkAdapters.FMLBench.runner._git_commit", lambda _path: ("a" * 40, False)
    )
    monkeypatch.setattr(
        "BenchmarkAdapters.FMLBench.agents.base._git_identity",
        lambda _path: ("a" * 40, False),
    )
    monkeypatch.setenv("SYNTHETIC_API_KEY", "synthetic-secret")
    variant = "g3@" + "a" * 40 if agent == "ear" else "synthetic@" + "a" * 40
    request = FMLRunRequest(
        agent=agent,
        protocol=protocol,
        model_config=model_config,
        task_config=protocol.task_config_paths[0],
        output_dir=campaign / agent / f"run-{outer_run_index}" / "synthetic",
        outer_run_index=outer_run_index,
        agent_variant=variant,
        formal=formal,
        credential_env_names=("SYNTHETIC_API_KEY",),
        gpu_ids=("0",),
        runtime_executable=fake_runtime,
    )
    return run_fml_task(request, _hardware=_fake_hardware())


def test_all_seven_agents_have_concrete_adapter_classes() -> None:
    assert tuple(FML_AGENT_ADAPTERS) == tuple(AGENTS)
    assert len(set(FML_AGENT_ADAPTERS.values())) == 7
    for agent_id, adapter_class in FML_AGENT_ADAPTERS.items():
        assert issubclass(adapter_class, FMLAgentAdapter)
        assert adapter_class.agent_id == agent_id
        assert adapter_class.native_entrypoint


def test_protocol_contains_code_owned_adapters_not_commands(tmp_path: Path) -> None:
    protocol = _protocol(tmp_path)
    payload = protocol.to_dict()
    serialized = json.dumps(payload)
    assert "launcher_commands" not in payload
    assert "SET_CONCRETE_COMMAND" not in serialized
    assert tuple(payload["agent_adapter_ids"]) == tuple(AGENTS)


def test_shared_runner_has_no_agent_specific_dispatch() -> None:
    source = (ROOT / "BenchmarkAdapters/FMLBench/runner.py").read_text(encoding="utf-8")
    for agent_id in AGENTS:
        assert f'agent == "{agent_id}"' not in source
        assert f'agent == \'{agent_id}\'' not in source


@pytest.mark.parametrize("agent", tuple(AGENTS))
def test_each_adapter_installation_probe_is_explicit(agent: str) -> None:
    report = get_fml_agent_adapter(agent).validate_installation()
    assert report.agent_id == agent
    assert report.native_entrypoint == FML_AGENT_ADAPTERS[agent].native_entrypoint
    assert isinstance(report.ready, bool)
    if report.ready:
        assert report.executable_path
        assert report.executable_sha256 and len(report.executable_sha256) == 64
    else:
        assert report.failure_reason


@pytest.mark.parametrize("agent", tuple(AGENTS))
def test_each_adapter_builds_same_model_command(
    tmp_path: Path, agent: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    protocol = _protocol(tmp_path)
    fake_runtime = _write_fake_runtime(tmp_path / "fake-agent")
    task = load_fml_task(protocol, protocol.task_config_paths[0])
    from BenchmarkAdapters.FMLBench.agents.base import FMLAgentLaunchContext
    from BenchmarkAdapters.FMLBench.workspace import FMLWorkspace

    workspace = FMLWorkspace.create(
        upstream_root=protocol.upstream_root,
        task=task,
        destination=tmp_path / "workspace-copy",
    )
    monkeypatch.setenv("SYNTHETIC_API_KEY", "synthetic-secret")
    context = FMLAgentLaunchContext(
        agent_id=agent,
        agent_variant=("g3@" if agent == "ear" else "synthetic@") + "a" * 40,
        task=task,
        workspace=workspace,
        output_dir=tmp_path / "agent-output",
        development_socket=tmp_path / "dev.sock",
        development_token="token",
        development_client_path=ROOT / "BenchmarkAdapters/FMLBench/dev_client.py",
        model_config=_model_config("http://127.0.0.1:9999/v1"),
        outer_run_id=0,
        timeout_seconds=60,
        credential_env_names=("SYNTHETIC_API_KEY",),
        relay_base_url="http://127.0.0.1:9999/v1",
        runtime_executable=fake_runtime,
    )
    adapter = get_fml_agent_adapter(agent)
    command, digest = adapter.build_launch_command(context)
    prompt = adapter.render_task_input(context)
    auditable_prompt = adapter.render_auditable_task_input(context)
    assert command.argv[0] == str(fake_runtime.resolve())
    assert command.env["FML_MODEL_ID"] == "synthetic-model-id"
    assert command.env["OPENAI_BASE_URL"] == "http://127.0.0.1:9999/v1"
    assert command.timeout_seconds == protocol.wall_clock_seconds
    assert hashlib.sha256(auditable_prompt.encode()).hexdigest() == digest
    assert task.task_description in prompt
    assert "--token token" not in auditable_prompt


def test_rendered_task_audit_is_semantically_identical(tmp_path: Path) -> None:
    protocol = _protocol(tmp_path)
    task = load_fml_task(protocol, protocol.task_config_paths[0])
    report = rendered_instruction_audit(task, development_command="fml-dev")
    assert report["semantic_payload_identical"] is True
    assert report["hidden_agent_specific_task_advice"] is False
    assert len(set(report["rendered_prompt_digests"].values())) == 1


def test_seven_adapter_fake_relay_synthetic_e2e(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    protocol = _protocol(tmp_path)
    fake_runtime = _write_fake_runtime(tmp_path / "fake-agent")
    campaign = tmp_path / "campaign"
    with capture_relay() as relay:
        model_config = _model_config(relay.base_url)
        for agent in AGENTS:
            record = _run_cell(
                protocol=protocol,
                model_config=model_config,
                fake_runtime=fake_runtime,
                campaign=campaign,
                agent=agent,
                outer_run_index=0,
                monkeypatch=monkeypatch,
            )
            assert record.score_valid is True
            assert record.normalized_improvement == pytest.approx(0.1)
            assert record.win is True
            aggregate = aggregate_fml(protocol=protocol, campaign_dir=campaign, agent=agent)
            assert aggregate["reporting_label"] == "single_run"
            assert aggregate["metrics"]["average_improvement"]["mean"] == pytest.approx(0.1)
            assert aggregate["metrics"]["average_improvement"]["standard_deviation"] is None
            assert aggregate["metrics"]["win_rate"]["mean"] == pytest.approx(1.0)
    assert len(relay.requests) == 7
    assert {request.model for request in relay.requests} == {"synthetic-model-id"}
    serialized = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in campaign.rglob("*")
        if path.is_file() and path.suffix in {".json", ".log"}
    ).lower()
    assert "synthetic-secret" not in serialized
    assert "authorization:" not in serialized


def test_outer_repetitions_three_is_avg_at_three(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    protocol = _protocol(tmp_path, outer_repetitions=3)
    fake_runtime = _write_fake_runtime(tmp_path / "fake-agent")
    campaign = tmp_path / "campaign"
    with capture_relay() as relay:
        for run_index in range(3):
            _run_cell(
                protocol=protocol,
                model_config=_model_config(relay.base_url),
                fake_runtime=fake_runtime,
                campaign=campaign,
                agent="codex",
                outer_run_index=run_index,
                monkeypatch=monkeypatch,
            )
    aggregate = aggregate_fml(protocol=protocol, campaign_dir=campaign, agent="codex")
    assert aggregate["reporting_label"] == "avg_at_3"
    assert aggregate["metrics"]["average_improvement"]["standard_deviation"] == 0.0


@pytest.mark.parametrize(
    "target",
    ("artifact", "evaluation", "record", "agent_result", "manifest"),
)
def test_aggregate_rejects_tampered_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
) -> None:
    protocol = _protocol(tmp_path)
    fake_runtime = _write_fake_runtime(tmp_path / "fake-agent")
    campaign = tmp_path / "campaign"
    with capture_relay() as relay:
        _run_cell(
            protocol=protocol,
            model_config=_model_config(relay.base_url),
            fake_runtime=fake_runtime,
            campaign=campaign,
            agent="codex",
            outer_run_index=0,
            monkeypatch=monkeypatch,
        )
    run_dir = campaign / "codex/run-0/synthetic"
    paths = {
        "artifact": run_dir / "final-artifact.tar",
        "evaluation": run_dir / "evaluations/heldout-0001/evaluation-record.json",
        "record": run_dir / "task-record.json",
        "agent_result": run_dir / "agent-result.json",
        "manifest": run_dir / "manifest.json",
    }
    paths[target].write_bytes(paths[target].read_bytes() + b"tampered")
    with pytest.raises(AdapterError):
        aggregate_fml(protocol=protocol, campaign_dir=campaign, agent="codex")


def test_incomplete_task_set_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    protocol = _protocol(tmp_path)
    campaign = tmp_path / "campaign"
    with pytest.raises(AdapterError, match="missing task record"):
        aggregate_fml(protocol=protocol, campaign_dir=campaign, agent="codex")


def test_legacy_smoke_cannot_enter_formal_aggregate(tmp_path: Path) -> None:
    protocol = _protocol(tmp_path)
    run_dir = tmp_path / "campaign/codex/run-0/synthetic"
    run_dir.mkdir(parents=True)
    (run_dir / "task-record.json").write_text(
        json.dumps({"schema_version": 1, "non_formal": True, "raw_score": 1.0}),
        encoding="utf-8",
    )
    with pytest.raises(AdapterError):
        aggregate_fml(protocol=protocol, campaign_dir=tmp_path / "campaign", agent="codex")


def test_max_steps_one_is_rejected_for_formal(tmp_path: Path) -> None:
    with pytest.raises(AdapterError, match="max_steps=1"):
        _protocol(tmp_path, max_agent_steps=1)


def test_missing_upstream_identity_is_rejected(tmp_path: Path) -> None:
    protocol = _protocol(tmp_path)
    with pytest.raises(AdapterError, match="immutable upstream commit"):
        replace(protocol, upstream_commit="").validate(formal=True)


def test_dirty_upstream_is_rejected(tmp_path: Path) -> None:
    protocol = _protocol(tmp_path)
    (protocol.upstream_root / "dirty.txt").write_text("dirty", encoding="utf-8")
    with pytest.raises(AdapterError, match="clean pinned upstream"):
        protocol.validate(formal=True)


def test_missing_primary_metric_is_rejected(tmp_path: Path) -> None:
    protocol = _protocol(tmp_path)
    with pytest.raises(AdapterError, match="primary metric remains unfrozen"):
        replace(
            protocol,
            primary_metric_name="<PRIMARY_METRIC_REQUIRES_REVIEW>",
            formal_status="review-required",
        ).validate(formal=True)


def test_placeholder_or_incomplete_config_is_rejected(tmp_path: Path) -> None:
    protocol = _protocol(tmp_path)
    with pytest.raises(AdapterError):
        replace(protocol, internal_round_policy="<ROUND_POLICY>").validate(formal=False)
    with pytest.raises(AdapterError, match="allowlist is incomplete"):
        replace(protocol, shared_adapter_files={}).validate(formal=False)


def test_hardcoded_model_not_present_in_agent_adapters() -> None:
    for path in (ROOT / "BenchmarkAdapters/FMLBench/agents").glob("*.py"):
        source = path.read_text(encoding="utf-8").lower()
        assert "gpt-5.5" not in source
        assert "claude-opus" not in source


def test_formal_model_track_has_no_silent_fallback(tmp_path: Path) -> None:
    protocol = _protocol(tmp_path)
    model = replace(_model_config("http://relay.invalid/v1"), outer_model_id="")
    with pytest.raises(AdapterError):
        model.validate(formal=True)
    assert protocol.protocol_frozen is True


def test_formal_variant_requires_immutable_matching_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    protocol = _protocol(tmp_path)
    fake_runtime = _write_fake_runtime(tmp_path / "fake-agent")
    task = load_fml_task(protocol, protocol.task_config_paths[0])
    from BenchmarkAdapters.FMLBench.agents.base import FMLAgentLaunchContext
    from BenchmarkAdapters.FMLBench.workspace import FMLWorkspace

    workspace = FMLWorkspace.create(
        upstream_root=protocol.upstream_root,
        task=task,
        destination=tmp_path / "workspace-copy",
    )
    context = FMLAgentLaunchContext(
        agent_id="codex",
        agent_variant="codex@" + "b" * 40,
        task=task,
        workspace=workspace,
        output_dir=tmp_path / "agent-output",
        development_socket=tmp_path / "dev.sock",
        development_token="token",
        development_client_path=ROOT / "BenchmarkAdapters/FMLBench/dev_client.py",
        model_config=_model_config("http://relay.invalid/v1"),
        outer_run_id=0,
        timeout_seconds=60,
        credential_env_names=(),
        relay_base_url="http://127.0.0.1:6200/v1",
        runtime_executable=None,
        formal=True,
    )
    monkeypatch.setattr(
        "BenchmarkAdapters.FMLBench.agents.base._git_identity",
        lambda _path: ("a" * 40, False),
    )
    with pytest.raises(AdapterError, match="differs from the source identity"):
        get_fml_agent_adapter("codex").identity(context)


def test_readiness_is_honest_and_never_claims_scored() -> None:
    readiness = collect_fml_readiness()
    assert readiness["adapter_defined"] == {"count": 7, "total": 7, "complete": True}
    assert readiness["formal_scored"] is False
    assert readiness["formal_preflight_ready"] is False


def test_formal_preflight_rejects_unfrozen_primary_metric(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    protocol = _protocol(tmp_path)
    candidate = replace(
        protocol,
        primary_metric_name="<PRIMARY_METRIC_REQUIRES_REVIEW>",
        formal_status="review-required",
    )
    protocol_path = tmp_path / "protocol.json"
    candidate.write(protocol_path)
    model_path = tmp_path / "model.json"
    write_json_exclusive(model_path, _model_config("http://relay.invalid/v1").to_dict())
    monkeypatch.setattr(
        "BenchmarkAdapters.formal_preflight.git_identity", lambda _path: ("a" * 40, False)
    )
    report = collect_formal_preflight(
        benchmark_id="fml-bench",
        agent_id="codex",
        agent_variant="synthetic@" + "a" * 40,
        protocol_path=protocol_path,
        model_config_path=model_path,
        formal=True,
    )
    assert report.passed is False
    checks = {check.name: check for check in report.checks}
    assert checks["protocol_assets_verified"].passed is False


def test_canonical_task_and_prompt_digests_change_on_semantic_change(tmp_path: Path) -> None:
    protocol = _protocol(tmp_path)
    task = load_fml_task(protocol, protocol.task_config_paths[0])
    changed = replace(task, task_description=task.task_description + " changed")
    assert task.digest != changed.digest
    assert hashlib.sha256(task.render(development_command="dev").encode()).hexdigest() != hashlib.sha256(
        changed.render(development_command="dev").encode()
    ).hexdigest()
