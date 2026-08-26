from __future__ import annotations

import json
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from BenchmarkAdapters.contracts import CommandSpec
from BenchmarkAdapters.registry import AGENTS, ROOT
from BenchmarkAdapters.thin_registry import terminal_ao_agents
from BenchmarkAdapters.TerminalAO.baseline import BaselineManifest, tree_digest
from BenchmarkAdapters.TerminalAO.aggregate import aggregate_terminal_ao
from BenchmarkAdapters.TerminalAO.dev_client import request_evaluation
from BenchmarkAdapters.TerminalAO.dev_server import CandidateDevBroker
from BenchmarkAdapters.TerminalAO.evaluator import EvaluationRecord, aggregate_task_evaluations
from BenchmarkAdapters.TerminalAO.launchers import NativeAOLaunchRequest, build_native_ao_command
from BenchmarkAdapters.TerminalAO.launchers import sandbox_native_ao_command
from BenchmarkAdapters.process import run_command
from BenchmarkAdapters.TerminalAO.protocol import TerminalAOProtocol
from BenchmarkAdapters.TerminalAO.revisions import RevisionStore
from BenchmarkAdapters.TerminalAO.split import FrozenSplit
from BenchmarkAdapters.TerminalAO.supervisor import run_terminal_ao, summarize_token_log


ASSET_DIR = ROOT / "terminal-bench-2/ao_protocol"


def _protocol() -> TerminalAOProtocol:
    return TerminalAOProtocol.load(ASSET_DIR / "protocol.json")


def _fake_evaluation(
    protocol: TerminalAOProtocol,
    *,
    split_name: str,
    harness_dir: Path,
    evaluation_dir: Path,
    environment=None,
) -> EvaluationRecord:
    del environment
    split = FrozenSplit.load(protocol.split_path)
    task_ids = split.dev if split_name == "dev" else split.test
    marker = "synthetic-improvement" in (harness_dir / "terminus_2.py").read_text(
        encoding="utf-8"
    )
    passed = 2 if marker else 1
    evaluations = {
        task_id: __import__(
            "BenchmarkAdapters.TerminalAO.evaluator", fromlist=["TaskEvaluation"]
        ).TaskEvaluation(task_id, 1.0, "completed")
        for task_id in task_ids[:passed]
    }
    evaluation_dir.mkdir(parents=True, exist_ok=False)
    return aggregate_task_evaluations(
        protocol_id=protocol.protocol_id,
        protocol_digest=protocol.digest,
        split=split_name,
        split_digest=split.digest,
        candidate_digest=tree_digest(harness_dir),
        expected_task_ids=task_ids,
        evaluations=evaluations,
    )


def test_dev_socket_exposes_only_structured_dev_feedback(tmp_path: Path, monkeypatch) -> None:
    protocol = _protocol()
    store = RevisionStore(
        baseline_source=protocol.baseline_source,
        baseline_manifest=BaselineManifest.load(protocol.baseline_manifest_path),
        state_dir=tmp_path / "state",
    )
    candidate = store.checkout("baseline", "active")
    monkeypatch.setattr(
        "BenchmarkAdapters.TerminalAO.dev_server.evaluate_harness", _fake_evaluation
    )
    broker = CandidateDevBroker(
        protocol=protocol,
        revision_store=store,
        candidate_dir=candidate,
        output_dir=tmp_path / "evaluations",
        socket_path=tmp_path / "broker.sock",
    )
    with broker:
        payload = request_evaluation(str(broker.socket_path), broker.token)
    assert payload["expected_tasks"] == 36
    assert payload["passed"] == 1
    assert "tasks" not in payload
    assert "test" not in payload
    assert len(broker.calls) == 1


@pytest.mark.parametrize("agent", terminal_ao_agents())
def test_every_ao_launcher_dispatches_to_a_distinct_native_loop(
    agent: str,
    tmp_path: Path,
) -> None:
    request = NativeAOLaunchRequest(
        agent=agent,
        candidate_dir=tmp_path,
        launcher_output_dir=tmp_path / "launcher",
        dev_client_path=ROOT / "BenchmarkAdapters/TerminalAO/dev_client.py",
        dev_socket=tmp_path / "dev.sock",
        dev_token="capability-token",
        model="gpt-5.5",
        seed=0,
        timeout_seconds=172800,
        model_parameters={},
        request_timeout_seconds=60,
        retry_policy={},
    )
    command = build_native_ao_command(request)
    text = " ".join(command.argv)
    assert command.label == {
        "ear": "EAR native graph/Thompson Terminal AO loop",
        "arbor": "Arbor native coordinator Terminal AO loop",
        "codex": "Codex native Terminal AO loop",
        "claude-code": "Claude Code native Terminal AO loop",
        "ai-scientist": "AiScientist native subagent Terminal AO loop",
    }[agent]
    assert "dev.sock" in text
    assert "held-out-53" not in text
    assert "split.json" not in text
    if agent in {"ear", "ai-scientist"}:
        assert f"BenchmarkAdapters.TerminalAO.launchers.{agent.replace('-', '_')}" in text


def test_ear_is_bound_to_clean_g3_worktree() -> None:
    ear = AGENTS["ear"].install_path
    assert ear.name == "efficient-auto-research"
    assert subprocess.run(
        ["git", "-C", str(ear), "branch", "--show-current"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip() == "ear/g3"
    assert subprocess.run(
        ["git", "-C", str(ear), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip() == "7cd9ed5c1db0ff5250faad373e5d5a67209e604c"


def test_supervisor_scores_agent_declared_harness_then_consumes_one_sealed_test(
    tmp_path: Path,
    monkeypatch,
) -> None:
    protocol = _protocol()
    monkeypatch.setattr(
        "BenchmarkAdapters.TerminalAO.dev_server.evaluate_harness", _fake_evaluation
    )
    monkeypatch.setattr(
        "BenchmarkAdapters.TerminalAO.supervisor.evaluate_harness", _fake_evaluation
    )

    def synthetic_native_command(request: NativeAOLaunchRequest) -> CommandSpec:
        script = (
            "printf '\\n# synthetic-improvement\\n' >> terminus_2.py; "
            + request.dev_command
        )
        return CommandSpec(
            argv=("/bin/sh", "-c", script),
            cwd=request.candidate_dir,
            timeout_seconds=30,
            label="synthetic shipped-supervisor seam",
        )

    monkeypatch.setattr(
        "BenchmarkAdapters.TerminalAO.supervisor.build_native_ao_command",
        synthetic_native_command,
    )
    output = tmp_path / "run"
    result = run_terminal_ao(
        agent="codex",
        protocol=protocol,
        output_dir=output,
        seed=0,
        model="gpt-5.5",
        timeout_seconds=172800,
        formal=False,
    )
    selection = json.loads((output / "selection.json").read_text())
    assert selection["dev_evaluations"] == 2
    assert selection["selection_uses_test"] is False
    assert selection["selection_policy_id"] == "agent-declared"
    assert selection["harness_selected_among_candidates"] is False
    assert result.metrics["selection_policy"] == "agent-declared"
    assert result.score == pytest.approx(2 / 53)
    assert result.metrics["direct_89_score_used"] is False
    assert (output / "sealed/test-consumed.json").is_file()
    assert (output / "artifacts/final/harness.tar").is_file()


def test_supervisor_timeout_closes_search_and_scores_declared_harness(
    tmp_path: Path,
    monkeypatch,
) -> None:
    protocol = _protocol()
    monkeypatch.setattr(
        "BenchmarkAdapters.TerminalAO.dev_server.evaluate_harness", _fake_evaluation
    )
    monkeypatch.setattr(
        "BenchmarkAdapters.TerminalAO.supervisor.evaluate_harness", _fake_evaluation
    )
    monkeypatch.setattr(
        "BenchmarkAdapters.TerminalAO.supervisor.build_native_ao_command",
        lambda request: CommandSpec(
            argv=("/bin/true",),
            cwd=request.candidate_dir,
            timeout_seconds=1,
            label="synthetic timeout",
        ),
    )
    monkeypatch.setattr(
        "BenchmarkAdapters.TerminalAO.supervisor.run_command",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("native loop timed out")),
    )
    output = tmp_path / "timed-out-run"
    result = run_terminal_ao(
        agent="codex",
        protocol=protocol,
        output_dir=output,
        seed=0,
        model="gpt-5.5",
        timeout_seconds=172800,
        formal=False,
    )
    selection = json.loads((output / "selection.json").read_text())
    assert selection["launcher_timed_out"] is True
    assert result.score_valid is True
    assert result.metrics["launcher_timed_out_at_budget"] is True
    assert (output / "sealed/test-consumed.json").is_file()


def test_supervisor_exit_124_closes_search_and_records_timeout(
    tmp_path: Path,
    monkeypatch,
) -> None:
    protocol = _protocol()
    monkeypatch.setattr(
        "BenchmarkAdapters.TerminalAO.dev_server.evaluate_harness", _fake_evaluation
    )
    monkeypatch.setattr(
        "BenchmarkAdapters.TerminalAO.supervisor.evaluate_harness", _fake_evaluation
    )
    monkeypatch.setattr(
        "BenchmarkAdapters.TerminalAO.supervisor.build_native_ao_command",
        lambda request: CommandSpec(
            argv=("/bin/sh", "-c", "exit 124"),
            cwd=request.candidate_dir,
            timeout_seconds=1,
            label="synthetic timeout exit",
        ),
    )
    output = tmp_path / "exit-124-run"
    result = run_terminal_ao(
        agent="codex",
        protocol=protocol,
        output_dir=output,
        seed=0,
        model="gpt-5.5",
        timeout_seconds=172800,
        formal=False,
    )
    selection = json.loads((output / "selection.json").read_text())
    assert selection["launcher_return_code"] == 124
    assert selection["launcher_timed_out"] is True
    assert result.score_valid is True
    assert result.metrics["launcher_timed_out_at_budget"] is True
    assert (output / "sealed/test-consumed.json").is_file()


def test_formal_launcher_sandbox_hides_protocol_and_test_assets(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate"
    output = tmp_path / "launcher"
    candidate.mkdir()
    (candidate / "terminus_2.py").write_text("baseline\n")
    dev_socket_path = tmp_path / "dev.sock"
    relay_socket_path = tmp_path / "relay.sock"
    dev_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    relay_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    dev_socket.bind(str(dev_socket_path))
    relay_socket.bind(str(relay_socket_path))
    resolver = tmp_path / "resolv.conf"
    resolver.write_text("nameserver 10.0.2.3\n")
    hidden_root = str(ROOT / "terminal-bench-2")
    command = CommandSpec(
        argv=(
            "/bin/sh",
            "-c",
            f"test -f terminus_2.py && test -S /capability/dev.sock && "
            f"test ! -e {hidden_root} && test ! -e {ROOT / 'mle-bench-data'}",
        ),
        cwd=candidate,
        label="sandbox visibility seam",
    )
    try:
        sandboxed = sandbox_native_ao_command(
            agent="codex",
            command=command,
            candidate_dir=candidate,
            launcher_output_dir=output,
            host_dev_socket=dev_socket_path,
            host_relay_socket=relay_socket_path,
            resolver_path=resolver,
        )
        result = run_command(sandboxed)
    finally:
        dev_socket.close()
        relay_socket.close()
    assert result.return_code == 0, result.stdout
    assert sandboxed.inherit_env is False
    mount_arguments = sandboxed.argv[: sandboxed.argv.index("--chdir")]
    assert hidden_root not in mount_arguments
    assert str(ROOT / "mle-bench-data") not in mount_arguments


def test_terminal_ao_aggregate_reports_avg_at_three_and_failure_zero(tmp_path: Path) -> None:
    protocol = _protocol()
    for seed, passed in ((0, 10), (1, 20)):
        run_dir = tmp_path / "codex" / f"seed-{seed}"
        run_dir.mkdir(parents=True)
        (run_dir / "result.json").write_text(
            json.dumps(
                {
                    "protocol_digest": protocol.digest,
                    "mode": "terminal-ao",
                    "agent": "codex",
                    "seed": seed,
                    "status": "completed",
                    "score_valid": True,
                    "score": passed / 53,
                    "metrics": {
                        "passed": passed,
                        "failed": 53 - passed,
                        "errors": 0,
                        "missing_rewards": 0,
                    },
                    "tokens": {"input": 100},
                    "cost": {"usd": 1.0},
                    "wall_clock_seconds": 10,
                }
            )
        )
    aggregate = aggregate_terminal_ao(
        protocol=protocol,
        campaign_dir=tmp_path,
        agent="codex",
    )
    assert aggregate["tasks_per_seed"] == 53
    assert aggregate["num_seeds"] == 3
    assert aggregate["invalid_or_missing_seeds"] == 1
    assert aggregate["seed_metrics"][2]["pass_rate"] == 0.0
    assert aggregate["metrics"]["held_out_53_pass_rate"]["mean"] == pytest.approx(30 / 159)
    assert aggregate["direct_89_scores_included"] is False


def test_relay_token_log_is_summed_without_inventing_cost(tmp_path: Path) -> None:
    path = tmp_path / "tokens.jsonl"
    path.write_text(
        "\n".join(
            json.dumps(item)
            for item in (
                {
                    "input_tokens": 10,
                    "output_tokens": 2,
                    "cache_tokens": 4,
                    "reasoning_tokens": 1,
                    "retries": 0,
                },
                {
                    "input_tokens": 20,
                    "output_tokens": 3,
                    "cache_tokens": 5,
                    "reasoning_tokens": 2,
                    "retries": 1,
                },
            )
        )
        + "\n"
    )
    assert summarize_token_log(path) == {
        "input": 30,
        "output": 5,
        "cache": 9,
        "reasoning": 3,
        "requests": 2,
        "retries": 1,
    }


@pytest.mark.parametrize(
    ("agent", "python", "module", "native_symbol"),
    [
        (
            "ear",
            ROOT / "BenchmarkAdapters/environments/mle/ear/.venv/bin/python",
            "BenchmarkAdapters.TerminalAO.launchers.ear",
            "agent.engine.thompson.select_parent",
        ),
        (
            "ai-scientist",
            ROOT / "baselines/AiScientist/.venv/bin/python",
            "BenchmarkAdapters.TerminalAO.launchers.ai_scientist",
            "aisci_agent_runtime.subagents.terminal_task.TerminalTaskSubagent.run",
        ),
    ],
)
def test_python_launchers_import_their_native_control_loop(
    agent: str,
    python: Path,
    module: str,
    native_symbol: str,
) -> None:
    source_root = {
        "ear": AGENTS[agent].install_path,
        "ai-scientist": AGENTS[agent].install_path / "src",
    }[agent]
    source_file = ROOT / "BenchmarkAdapters/TerminalAO/launchers" / f"{module.rsplit('.', 1)[-1]}.py"
    source = source_file.read_text(encoding="utf-8")
    completed = subprocess.run(
        [str(python), "-c", f"import {module}; print('native-import-ok')"],
        cwd=ROOT,
        env={"PYTHONPATH": f"{ROOT}:{source_root}", "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "native-import-ok" in completed.stdout
    assert native_symbol.rsplit(".", 2)[-2] in source or native_symbol.rsplit(".", 1)[-1] in source


@pytest.mark.parametrize(
    ("agent", "expected_python", "variant"),
    [
        ("ear", ROOT / "BenchmarkAdapters/environments/mle/ear/.venv/bin/python", "default"),
        (
            "ai-scientist",
            ROOT / "baselines/AiScientist/.venv/bin/python",
            "ai-scientist-terminal-variant",
        ),
    ],
)
def test_python_launchers_preserve_virtual_environment_entrypoint(
    agent: str,
    expected_python: Path,
    variant: str,
    tmp_path: Path,
) -> None:
    command = build_native_ao_command(
        NativeAOLaunchRequest(
            agent=agent,
            candidate_dir=tmp_path,
            launcher_output_dir=tmp_path / "launcher",
            dev_client_path=ROOT / "BenchmarkAdapters/TerminalAO/dev_client.py",
            dev_socket=tmp_path / "dev.sock",
            dev_token="capability-token",
            model="gpt-5.5",
            seed=0,
            timeout_seconds=60,
            model_parameters={},
            request_timeout_seconds=60,
            retry_policy={},
            agent_variant=variant,
        )
    )
    assert command.argv[0] == str(expected_python)
