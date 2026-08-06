from __future__ import annotations

from pathlib import Path

from BenchmarkAdapters.MLEBenchLite.adapter import MleLiteAdapter, MleLiteRequest
from BenchmarkAdapters.RepositoryAgent.backend import RepositoryAgentBackend
from BenchmarkAdapters.RepositoryAgent.client import AssistantResponse
from BenchmarkAdapters.RepositoryAgent.contracts import RepositoryAgentRequest
from BenchmarkAdapters.RepositoryAgent.revisions import RevisionStore
from BenchmarkAdapters.RepositoryAgent.sandbox import RepositorySandbox
from BenchmarkAdapters.TerminalBench.adapter import (
    TerminalAoAdapter,
    TerminalAoRequest,
    parse_pass_rate,
    resolve_python_executable,
)


def _public_task(tmp_path: Path) -> Path:
    public = tmp_path / "demo" / "prepared" / "public"
    public.mkdir(parents=True)
    (public / "description.md").write_text("# Demo\n", encoding="utf-8")
    (public / "sample_submission.csv").write_text("id,target\n1,0\n", encoding="utf-8")
    return public


def test_codex_mle_adapter_reuses_public_workspace(tmp_path: Path) -> None:
    public = _public_task(tmp_path)
    request = MleLiteRequest(
        agent="codex",
        competition_id="demo",
        data_root=tmp_path,
        output_dir=tmp_path / "run",
    )
    command = MleLiteAdapter("codex").build_command(request)
    assert command.cwd == (tmp_path / "run" / "workspace").resolve()
    assert command.argv[0] == "codex"
    assert command.env["OPENAI_BASE_URL"].endswith("/v1")
    assert command.env["HTTPS_PROXY"] == "http://127.0.0.1:17892"
    assert public.is_dir()
    assert command.cwd.joinpath("input").is_symlink()
    assert command.cwd.joinpath("sample_submission.csv").is_file()


def test_terminal_ao_reuses_evaluator_contract(tmp_path: Path) -> None:
    harness = tmp_path / "harness"
    harness.mkdir()
    evaluator = harness / "run_eval.py"
    evaluator.write_text("print('7/10')\n", encoding="utf-8")
    dev = tmp_path / "dev.json"
    test = tmp_path / "test.json"
    dev.write_text("{}\n", encoding="utf-8")
    test.write_text("{}\n", encoding="utf-8")
    request = TerminalAoRequest(
        agent="codex",
        harness_dir=harness,
        eval_script=evaluator,
        dev_data=dev,
        test_data=test,
        output_dir=tmp_path / "run",
    )
    command = TerminalAoAdapter("codex").build_eval_command(request, "dev")
    assert command.argv[1:3] == ("-m", "BenchmarkAdapters.RepositoryAgent.evaluate")
    assert command.argv[command.argv.index("--data") + 1] == str(dev.resolve())
    assert command.argv[command.argv.index("--concurrency") + 1] == "8"
    assert parse_pass_rate("passed=7/10") == 0.7


def test_shared_terminal_agents_use_thin_launchers(tmp_path: Path) -> None:
    harness = tmp_path / "harness"
    harness.mkdir()
    evaluator = harness / "run_eval.py"
    evaluator.write_text("print('1/1')\n", encoding="utf-8")
    data = tmp_path / "data.json"
    data.write_text("{}\n", encoding="utf-8")
    for agent, module in {
        "ear": "launchers.ear",
        "mlevolve": "launchers.mlevolve",
        "ml-master-2": "launchers.ml_master_2",
        "ai-scientist": "launchers.ai_scientist",
    }.items():
        request = TerminalAoRequest(
            agent=agent,
            harness_dir=harness,
            eval_script=evaluator,
            dev_data=data,
            test_data=data,
            output_dir=tmp_path / f"run-{agent}",
        )
        command = TerminalAoAdapter(agent).build_optimizer_command(request)
        assert command.argv[1:3] == ("-m", f"BenchmarkAdapters.RepositoryAgent.{module}")
        assert "--dev-data" in command.argv
        assert "--protected-path" in command.argv
        assert command.cwd.name == "efficient-agent-research"


def test_parse_pass_rate_accepts_reward() -> None:
    assert parse_pass_rate("reward: 0.75") == 0.75


def test_auto_python_preserves_harness_venv_path(tmp_path: Path) -> None:
    harness = tmp_path / "harness"
    executable = harness / ".venv/bin/python"
    executable.parent.mkdir(parents=True)
    executable.symlink_to(Path("/usr/bin/python3"))
    (harness / ".venv/pyvenv.cfg").write_text("home = /usr/bin\n", encoding="utf-8")
    assert resolve_python_executable("auto", harness) == str(executable.absolute())


class _ScriptedClient:
    def __init__(self) -> None:
        self.responses = [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "edit",
                        "type": "function",
                        "function": {
                            "name": "replace_in_file",
                            "arguments": '{"path":"solution.py","old":"return False","new":"return True"}',
                        },
                    }
                ],
            },
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "evaluate",
                        "type": "function",
                        "function": {"name": "evaluate_dev", "arguments": "{}"},
                    }
                ],
            },
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "submit",
                        "type": "function",
                        "function": {"name": "submit_candidate", "arguments": "{}"},
                    }
                ],
            },
        ]

    def complete(self, _messages: list[dict], _tools: list[dict]) -> AssistantResponse:
        if len(self.responses) == 1:
            assert _messages[-1]["role"] == "tool"
            assert "dev_score=1.00000000" in _messages[-1]["content"]
        return AssistantResponse(self.responses.pop(0))


def test_repository_backend_applies_best_dev_candidate(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "solution.py").write_text(
        "def solved():\n    return False\n",
        encoding="utf-8",
    )
    evaluator = tmp_path / "run_eval.py"
    evaluator.write_text(
        "from pathlib import Path\n"
        "from solution import solved\n"
        "Path('evaluation-side-effect.txt').write_text('temporary')\n"
        "print('1/1' if solved() else '0/1')\n",
        encoding="utf-8",
    )
    dev_data = tmp_path / "dev.json"
    dev_data.write_text("{}\n", encoding="utf-8")
    held_out = tmp_path / "test.json"
    held_out.write_text("HELD_OUT_SECRET\n", encoding="utf-8")
    request = RepositoryAgentRequest(
        agent="mlevolve",
        repository=repository,
        evaluator=evaluator,
        dev_data=dev_data,
        protected_paths=(held_out,),
        output_dir=tmp_path / "output",
        instruction="Make solved() return true.",
        candidates=1,
        max_turns=3,
        command_timeout_seconds=30,
        python_executable="python3",
    )
    result = RepositoryAgentBackend(request, client=_ScriptedClient()).run()
    assert result["baseline_score"] == 0.0
    assert result["best_score"] == 1.0
    assert result["changed_files"] == ["solution.py"]
    assert "return True" in (repository / "solution.py").read_text(encoding="utf-8")
    assert not (repository / "evaluation-side-effect.txt").exists()
    assert held_out.read_text(encoding="utf-8") == "HELD_OUT_SECRET\n"


def test_repository_shell_cannot_read_host_paths(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "visible.txt").write_text("visible\n", encoding="utf-8")
    secret = tmp_path / "secret.txt"
    secret.write_text("HELD_OUT_SECRET\n", encoding="utf-8")
    sandbox = RepositorySandbox(workspace, command_timeout_seconds=10)
    visible = sandbox.run_shell("cat visible.txt")
    hidden = sandbox.run_shell(f"cat {secret}")
    network = sandbox.run_shell("test ! -e /proc/net/tcp || test ! -s /proc/net/tcp")
    assert visible.return_code == 0
    assert visible.output.strip() == "visible"
    assert hidden.return_code != 0
    assert "HELD_OUT_SECRET" not in hidden.output
    assert network.return_code == 0


def test_revision_store_materializes_rename(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "old.txt").write_text("value\n", encoding="utf-8")
    store = RevisionStore(repository, tmp_path / "state")
    baseline = store.initialize()
    workspace = store.checkout(baseline, "renamed")
    (workspace / "old.txt").rename(workspace / "new.txt")
    revision = store.commit(workspace, "rename")
    store.materialize(revision, repository, baseline)
    assert not (repository / "old.txt").exists()
    assert (repository / "new.txt").read_text(encoding="utf-8") == "value\n"


def test_revision_store_rejects_protected_parent_replacement(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    protected = repository / "tests" / "held-out.json"
    protected.parent.mkdir(parents=True)
    protected.write_text("held-out\n", encoding="utf-8")
    (repository / "safe.txt").write_text("safe\n", encoding="utf-8")
    store = RevisionStore(repository, tmp_path / "state", protected_paths=(protected,))
    baseline = store.initialize()
    workspace = store.checkout(baseline, "attack")
    tests_path = workspace / "tests"
    if tests_path.exists():
        tests_path.rmdir()
    tests_path.symlink_to(".")
    revision = store.commit(workspace, "replace protected parent")
    try:
        store.materialize(revision, repository, baseline)
    except Exception as exc:
        assert "protected path" in str(exc)
    else:
        raise AssertionError("protected parent replacement was accepted")
    assert protected.read_text(encoding="utf-8") == "held-out\n"
