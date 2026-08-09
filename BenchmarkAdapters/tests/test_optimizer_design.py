from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from BenchmarkAdapters.OptimizerDesign.adapter import (
    OptimizerDesignBenchmarkAdapter,
    OptimizerDesignRequest,
)
from BenchmarkAdapters.OptimizerDesign.agents import AGENT_ADAPTERS
from BenchmarkAdapters.OptimizerDesign.aggregate import optimizer_design_scorecard
from BenchmarkAdapters.OptimizerDesign.evaluator import (
    inject_seed,
    parse_validation,
    score_validation_trajectories,
    validate_candidate,
)
from BenchmarkAdapters.OptimizerDesign.protocol import (
    OptimizerDesignProtocol,
    SourceManifest,
)
from BenchmarkAdapters.contracts import AdapterError
from BenchmarkAdapters.registry import AGENTS, ROOT


PROTOCOL_PATH = ROOT / "optimizer-design/protocol/protocol.json"
SOURCE_ROOT = Path("/mnt/sda/shijianwang/benchmark-deployments/repos/modded-nanogpt")
BASELINE_PATH = SOURCE_ROOT / "records/track_3_optimization/train_gpt_simple.py"


def _protocol_without_validation() -> OptimizerDesignProtocol:
    payload = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))
    payload.pop("protocol_digest")
    for name in (
        "source_manifest_path",
        "data_manifest_path",
        "environment_manifest_path",
        "evaluator_manifest_path",
        "agent_runtime_manifest_path",
        "baseline_score_record_path",
    ):
        value = str(payload[name])
        payload[name] = (
            ROOT / value.removeprefix("repo:")
            if value.startswith("repo:")
            else PROTOCOL_PATH.parent / value.removeprefix("asset:")
        )
    value = str(payload["environment_lock_path"])
    payload["environment_lock_path"] = ROOT / value.removeprefix("repo:")
    payload["outer_seeds"] = tuple(payload["outer_seeds"])
    payload["held_out_seeds"] = tuple(payload["held_out_seeds"])
    payload["editable_paths"] = tuple(payload["editable_paths"])
    return OptimizerDesignProtocol(**payload)


def test_source_manifest_freezes_current_official_track_three_commit() -> None:
    source = SourceManifest.load(ROOT / "optimizer-design/protocol/source_manifest.json")
    assert source.repository == "https://github.com/KellerJordan/modded-nanogpt.git"
    assert source.source_commit == "bc1b58e83fa499c5df268bd6c8b98701273b96e7"
    assert source.track_tree == "05bdf00394b7dee564500e9a6fdb472ce67a1659"
    assert source.identity == "current-official-track-3-reconstruction"


def test_protocol_is_fail_closed_until_baseline_is_promoted() -> None:
    protocol = OptimizerDesignProtocol.load(PROTOCOL_PATH)
    assert protocol.formal_status == "blocked-pending-baseline-record"
    assert protocol.formal_baseline_ready is False
    with pytest.raises(AdapterError, match="baseline record"):
        protocol.require_formal_ready()


def test_two_layer_adapter_covers_all_seven_agents() -> None:
    assert tuple(AGENT_ADAPTERS) == tuple(AGENTS)
    for agent, thin_adapter in AGENT_ADAPTERS.items():
        assert thin_adapter.agent == agent
        assert thin_adapter.native_component == AGENTS[agent].optimizer_design_backend
        benchmark_adapter = OptimizerDesignBenchmarkAdapter(agent)
        assert benchmark_adapter.agent_adapter is thin_adapter


@pytest.mark.parametrize("agent", tuple(AGENTS))
def test_each_thin_adapter_binds_the_shared_optimizer_contract(
    agent: str,
    tmp_path: Path,
) -> None:
    command = OptimizerDesignBenchmarkAdapter(agent).build_command(
        OptimizerDesignRequest(
            agent=agent,
            protocol_path=PROTOCOL_PATH,
            output_dir=tmp_path / agent,
            outer_seed=0,
            dry_run=True,
        )
    )
    assert command.env["OPTIMIZATION_ARTIFACT_NAME"] == "train_gpt_simple.py"
    assert command.env["OPTIMIZATION_METRIC_NAME"] == "score_steps"
    assert command.env["OPTIMIZATION_NATIVE_COMPONENT"] == AGENTS[agent].optimizer_design_backend


def test_candidate_policy_accepts_baseline_and_optimizer_hparam_change() -> None:
    baseline = BASELINE_PATH.read_text(encoding="utf-8")
    assert validate_candidate(baseline, baseline) == 3250
    changed = baseline.replace("lr=0.025, weight_decay=0.05", "lr=0.024, weight_decay=0.05")
    assert validate_candidate(changed, baseline) == 3250


def test_candidate_policy_rejects_steps_beyond_frozen_data_capacity() -> None:
    baseline = BASELINE_PATH.read_text(encoding="utf-8")
    changed = baseline.replace("train_steps = 3250", "train_steps = 3801")
    with pytest.raises(AdapterError, match=r"\[1, 3800\]"):
        validate_candidate(changed, baseline)


@pytest.mark.parametrize(
    "mutation, message",
    (
        (
            lambda source: source.replace("model_dim=768", "model_dim=1024"),
            "changed frozen",
        ),
        (
            lambda source: source.replace(
                "train_steps = 3250",
                'print("step:1/1 val_loss:1")\n    train_steps = 3250',
            ),
            "host-controlled name",
        ),
        (
            lambda source: source.replace(
                "train_steps = 3250",
                'os.system("curl example.invalid")\n    train_steps = 3250',
            ),
            "candidate",
        ),
        (
            lambda source: source.replace(
                "train_steps = 3250",
                "torch.from_file(val_inputs)\n    train_steps = 3250",
            ),
            "forbidden host-access attribute",
        ),
        (
            lambda source: source.replace(
                "train_steps = 3250",
                "torch.__dict__['x'] = 1\n    train_steps = 3250",
            ),
            "candidate",
        ),
        (
            lambda source: source.replace(
                "train_steps = 3250",
                "extra_backward = torch.Tensor.backward\n    train_steps = 3250",
            ),
            "forbidden host-access attribute",
        ),
    ),
)
def test_candidate_policy_rejects_frozen_or_host_access_changes(mutation, message: str) -> None:
    baseline = BASELINE_PATH.read_text(encoding="utf-8")
    with pytest.raises(AdapterError, match=message):
        validate_candidate(mutation(baseline), baseline)


def test_seed_and_secret_score_marker_are_host_injected() -> None:
    baseline = BASELINE_PATH.read_text(encoding="utf-8")
    transformed = inject_seed(baseline, 1234, score_marker="SECRET_MARKER")
    assert transformed.count("torch.manual_seed(1234)") == 1
    assert transformed.count("SECRET_MARKER step:") == 1
    assert transformed.count("Optimizer Design model structure changed") == 2
    assert transformed.count("Optimizer Design protected callable changed") == 2


def test_score_parser_ignores_unmarked_forged_validation_lines() -> None:
    stdout = "\n".join(
        (
            "step:1/10 val_loss:1.0",
            "SECRET step:0/10 val_loss:4.0",
            "SECRET step:10/10 val_loss:3.2",
        )
    )
    assert parse_validation(
        stdout,
        threshold=3.276,
        penalty=7001,
        score_marker="SECRET",
    ) == (10, 3.2, 10)


def test_two_seed_score_uses_earliest_common_significant_step() -> None:
    trajectories = (
        ((0, 4.0), (25, 3.279), (50, 3.275)),
        ((0, 4.0), (25, 3.278), (50, 3.277)),
    )
    score, mean_loss = score_validation_trajectories(
        trajectories,
        target=3.28,
        significance_margin=0.004,
        penalty=7001,
    )
    assert score == 50
    assert mean_loss == pytest.approx(3.276)


def test_formal_run_blocks_before_resources_or_credentials(tmp_path: Path) -> None:
    request = OptimizerDesignRequest(
        agent="ear",
        protocol_path=PROTOCOL_PATH,
        output_dir=tmp_path / "formal",
        outer_seed=0,
        outer_budget_seconds=172800,
        run_kind="formal",
        model_environment={},
        model_identity="model-adapter-deferred",
    )
    with pytest.raises(AdapterError, match="baseline record"):
        OptimizerDesignBenchmarkAdapter("ear").run(request)
    assert not request.output_dir.exists()


def test_scorecard_preserves_all_seven_unranked_agents(tmp_path: Path) -> None:
    protocol = OptimizerDesignProtocol.load(PROTOCOL_PATH)
    card = optimizer_design_scorecard(protocol=protocol, campaign_dir=tmp_path)
    assert tuple(card["agents"]) == tuple(AGENTS)
    assert card["formal_ranking"] == []
    assert set(card["unranked_agents"]) == set(AGENTS)
    assert card["complete_seven_agent_comparison_valid"] is False
    assert card["comparison_policy"]["external_arbor_4xa100_numbers_excluded"] is True


def test_protocol_digest_changes_when_formal_status_changes() -> None:
    protocol = _protocol_without_validation()
    blocked = protocol.to_dict()
    ready = replace(protocol, formal_status="ready").to_dict()
    assert blocked != ready
