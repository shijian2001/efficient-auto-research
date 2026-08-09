from __future__ import annotations

import json
import socket
import sys
import time
from pathlib import Path

import pytest

from BenchmarkAdapters.AutoResearch.baseline import BaselineManifest
from BenchmarkAdapters.AutoResearch.broker import CandidateDevBroker
from BenchmarkAdapters.AutoResearch.evaluator import CandidateEvaluator, EvaluatorManifest
from BenchmarkAdapters.AutoResearch.launchers import (
    NativeCommandSearchRunner,
    NativeLaunchRequest,
    build_native_command,
)
from BenchmarkAdapters.AutoResearch.launchers.sandbox import sandbox_native_command
from BenchmarkAdapters.AutoResearch.model_adapters import model_identity
from BenchmarkAdapters.AutoResearch.protocol import build_protocol
from BenchmarkAdapters.AutoResearch.revisions import TrainRevisionStore
from BenchmarkAdapters.AutoResearch.search import SearchContext
from BenchmarkAdapters.contracts import CommandSpec
from BenchmarkAdapters.process import run_command
from BenchmarkAdapters.registry import AGENTS, ROOT


EXPECTED_LABELS = {
    "ear": "EAR native G3 KTS loop",
    "mlevolve": "MLEvolve native AgentSearch/UCT loop",
    "arbor": "Arbor native coordinator/executor loop",
    "codex": "Codex native repository loop",
    "claude-code": "Claude Code native repository loop",
    "ml-master-2": "ML-Master 2 native EvoMaster workflow",
    "ai-scientist": "AiScientist native Subagent loop",
}


EXPECTED_NATIVE_MARKERS = {
    "ear": "BenchmarkAdapters.AutoResearch.launchers.ear",
    "mlevolve": "BenchmarkAdapters.AutoResearch.launchers.mlevolve",
    "arbor": "arbor",
    "codex": "exec",
    "claude-code": "--no-session-persistence",
    "ml-master-2": "BenchmarkAdapters.AutoResearch.launchers.ml_master_2",
    "ai-scientist": "BenchmarkAdapters.AutoResearch.launchers.ai_scientist",
}


def _synthetic_train(score: float) -> str:
    return f"""class Cuda:
    def manual_seed(self, seed):
        pass
class Torch:
    cuda = Cuda()
    def manual_seed(self, seed):
        pass
torch = Torch()
torch.manual_seed(42)
torch.cuda.manual_seed(42)
print('val_bpb: {score}')
print('training_seconds: 300.0')
print('total_seconds: 301.0')
print('peak_vram_mb: 10.0')
print('mfu_percent: 1.0')
print('total_tokens_M: 1.0')
print('num_steps: 11')
print('num_params_M: 1.0')
print('depth: 1')
"""


@pytest.mark.parametrize("agent", tuple(AGENTS))
def test_every_autoresearch_dispatch_targets_a_distinct_native_component(
    agent: str,
    tmp_path: Path,
) -> None:
    request = NativeLaunchRequest(
        agent=agent,
        workspace=tmp_path,
        output_dir=tmp_path / "native",
        socket_path=tmp_path / "dev.sock",
        token="local-capability-token",
        outer_seed=0,
        timeout_seconds=172800,
        runtime_root=tmp_path / "runtime",
    )
    command = build_native_command(request)
    text = " ".join(command.argv)
    lower_text = text.lower()
    assert command.label == EXPECTED_LABELS[agent]
    assert EXPECTED_NATIVE_MARKERS[agent] in text
    assert "dev.sock" in text
    if agent in {"codex", "claude-code"}:
        assert "train.py" in text
        assert "held-out" in lower_text
    else:
        module_name = agent.replace("-", "_")
        module_source = (
            ROOT / "BenchmarkAdapters/AutoResearch/launchers" / f"{module_name}.py"
        ).read_text(encoding="utf-8")
        assert "artifact_path" in module_source or "task_contract" in module_source
        assert "dev_client" in module_source
    assert "314159" not in text
    assert "271828" not in text


def test_frozen_model_adapter_identity_does_not_expose_endpoint() -> None:
    identity = model_identity("synthetic-model-v1", "https://relay.example.invalid/v1")
    assert identity.startswith("openai-compatible:synthetic-model-v1:endpoint-")
    assert "relay.example.invalid" not in identity


def test_registered_backends_are_not_one_shared_prompt_profile() -> None:
    backends = [spec.autoresearch_backend for spec in AGENTS.values()]
    assert len(backends) == 7
    assert len(set(backends)) == 7
    source = {
        name: (ROOT / "BenchmarkAdapters/AutoResearch/launchers" / name).read_text(encoding="utf-8")
        for name in ("ear.py", "mlevolve.py", "ml_master_2.py", "ai_scientist.py")
    }
    assert "select_parent(graph, stagnation=stagnation, metric_sign=contract.metric_sign)" in source["ear.py"]
    assert "node_selection.select_with_soft_switch" in source["mlevolve.py"]
    assert "agent.run(" in source["ml_master_2.py"]
    assert "subagent.run(" in source["ai_scientist.py"]


def test_agent_sandbox_hides_protocol_and_exposes_only_workspace_runtime_and_dev_socket(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    runtime = tmp_path / "runtime"
    output = tmp_path / "native"
    workspace.mkdir()
    runtime.mkdir()
    (workspace / "train.py").write_text("print('candidate')\n", encoding="utf-8")
    socket_path = tmp_path / "dev.sock"
    server_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server_socket.bind(str(socket_path))
    hidden_protocol = ROOT / "autoresearch/protocol/seed_policy.json"
    command = CommandSpec(
        argv=(
            "/bin/sh",
            "-c",
            f"test -f train.py && test -S /capability/dev.sock && "
            f"test -d {runtime} && test ! -e {hidden_protocol}",
        ),
        cwd=workspace,
        label="Autoresearch sandbox visibility seam",
    )
    try:
        sandboxed = sandbox_native_command(
            agent="codex",
            command=command,
            workspace=workspace,
            output_dir=output,
            runtime_root=runtime,
            host_socket=socket_path,
        )
        result = run_command(sandboxed)
    finally:
        server_socket.close()
    assert result.return_code == 0, result.stdout
    assert sandboxed.inherit_env is False
    assert str(ROOT / "autoresearch") not in sandboxed.argv


@pytest.mark.parametrize("agent_index,agent", enumerate(tuple(AGENTS)))
def test_native_dispatch_can_create_score_and_declare_candidate_without_api(
    agent_index: int,
    agent: str,
    tmp_path: Path,
) -> None:
    protocol = build_protocol()
    store = TrainRevisionStore(
        baseline_source=protocol.source_root,
        baseline_manifest=BaselineManifest.load(protocol.baseline_manifest_path),
        state_dir=tmp_path / "state",
    )
    prepared = tmp_path / "prepared"
    prepared.mkdir()
    evaluator = CandidateEvaluator(
        manifest=EvaluatorManifest.load(protocol.evaluator_manifest_path),
        prepared_root=prepared,
        command_prefix=(sys.executable,),
        timeout_seconds=10,
    )
    broker = CandidateDevBroker(
        revision_store=store,
        evaluator=evaluator,
        dev_seed=42,
        output_dir=tmp_path / "dev",
    )
    captured: list[CommandSpec] = []

    def local_builder(request: NativeLaunchRequest) -> CommandSpec:
        captured.append(build_native_command(request))
        source = _synthetic_train(1.10 - agent_index * 0.01)
        script = (
            "from pathlib import Path; "
            "from BenchmarkAdapters.AutoResearch.dev_client import evaluate_current, declare_current; "
            f"workspace=Path({str(request.workspace)!r}); "
            f"workspace.joinpath('train.py').write_text({source!r}, encoding='utf-8'); "
            f"evaluate_current({str(request.socket_path)!r}, {request.token!r}, "
            f"workspace/'train.py', Path({str(request.state_path)!r})); "
            f"declare_current({str(request.socket_path)!r}, {request.token!r}, "
            f"Path({str(request.state_path)!r}))"
        )
        return CommandSpec(
            argv=(sys.executable, "-c", script),
            cwd=request.workspace,
            env={"PYTHONPATH": str(ROOT)},
            timeout_seconds=30,
            label=f"local deterministic seam for {request.agent}",
        )

    runner = NativeCommandSearchRunner(command_builder=local_builder)
    outcome = runner(
        SearchContext(
            agent=agent,
            native_backend=AGENTS[agent].autoresearch_backend,
            outer_seed=0,
            outer_deadline_monotonic=time.monotonic() + 30,
            candidate_training_seconds=300,
            program_path=protocol.source_root / "program.md",
            baseline_train_path=store.get("baseline").path / "train.py",
            output_dir=tmp_path / "launcher",
            broker=broker,
        )
    )
    assert outcome.completed is True
    assert outcome.native_component == AGENTS[agent].autoresearch_backend
    assert outcome.declared_revision_id is not None
    assert broker.declared_revision_id == outcome.declared_revision_id
    assert broker.best is not None
    assert broker.best.evaluation.score_valid is True
    assert captured[0].label == EXPECTED_LABELS[agent]
    dispatch = json.loads((tmp_path / "launcher/native-dispatch.json").read_text(encoding="utf-8"))
    assert dispatch["native_component"] == AGENTS[agent].autoresearch_backend
