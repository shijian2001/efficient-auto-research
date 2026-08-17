from __future__ import annotations

from pathlib import Path
import subprocess
import importlib.util
import socketserver
import threading
from http.server import BaseHTTPRequestHandler

import pytest

from BenchmarkAdapters import UnsupportedAdapterError
from BenchmarkAdapters.contracts import AdapterError
from BenchmarkAdapters.cli import _command_payload
from BenchmarkAdapters.cli import cli_entrypoint
from BenchmarkAdapters.cli import main as adapter_cli_main
from BenchmarkAdapters.MLEBenchLite.adapter import (
    MleLiteAdapter,
    MleLiteRequest,
    MleLiteWorkspace,
    _workspace_sandbox_argv,
    find_submission,
)
from BenchmarkAdapters.TerminalBench.adapter import HarborTerminalAdapter, HarborTerminalRequest
from BenchmarkAdapters.registry import AGENTS


def _public_task(tmp_path: Path) -> Path:
    public = tmp_path / "demo" / "prepared" / "public"
    public.mkdir(parents=True)
    (public / "description.md").write_text("# Demo\n", encoding="utf-8")
    (public / "sample_submission.csv").write_text("id,target\n1,0\n", encoding="utf-8")
    return public


def _terminal_dataset(tmp_path: Path) -> Path:
    dataset = tmp_path / "terminal-bench-2"
    task = dataset / "demo-task"
    task.mkdir(parents=True)
    (task / "task.toml").write_text("version = '1.0'\n", encoding="utf-8")
    return dataset


def test_codex_mle_adapter_reuses_public_workspace(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "host-secret")
    public = _public_task(tmp_path)
    request = MleLiteRequest(
        agent="codex",
        competition_id="demo",
        data_root=tmp_path,
        output_dir=tmp_path / "run",
    )
    command = MleLiteAdapter("codex").build_command(
        MleLiteRequest(**{**request.__dict__, "dry_run": True})
    )
    assert command.cwd == (tmp_path / "run" / "workspace").resolve()
    assert command.argv[0] == "/usr/bin/bwrap"
    assert any(part.endswith("/codex") for part in command.argv)
    assert command.inherit_env is False
    assert command.env["OPENAI_BASE_URL"].endswith("/v1")
    assert command.env["OPENAI_API_KEY"] == "proxy"
    assert "host-secret" not in str(command.env)
    assert public.is_dir()
    assert not request.output_dir.exists()


def test_workspace_sandbox_hides_repository_and_mounts_public_read_only(
    tmp_path: Path,
) -> None:
    public = tmp_path / "public"
    public.mkdir()
    (public / "visible.txt").write_text("public", encoding="utf-8")
    workspace_dir = tmp_path / "workspace"
    (workspace_dir / "input").mkdir(parents=True)
    workspace = MleLiteWorkspace(
        competition_id="demo",
        public_dir=public,
        workspace_dir=workspace_dir,
        description_path=workspace_dir / "description.md",
        sample_submission_path=workspace_dir / "sample_submission.csv",
    )
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            body = b'{"status":"ok"}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
        daemon_threads = True

    socket_path = tmp_path / "relay.sock"
    server = Server(str(socket_path), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        argv = _workspace_sandbox_argv(
            Path("/bin/sh"),
            workspace,
            (
                "-c",
                "test ! -e /mnt/sdc/shijianwang/efficient-agent-research && "
                "test -r input/visible.txt && ! touch input/forbidden && "
                "/benchmark-venv/bin/python -c \"import urllib.request; "
                "urllib.request.urlopen('http://127.0.0.1:6200/health').read()\" && "
                "touch created.txt",
            ),
            relay_socket=socket_path,
        )
        subprocess.run(
            argv,
            check=True,
            env={"HOME": "/tmp/home", "PATH": "/usr/bin:/bin"},
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    assert (workspace_dir / "created.txt").is_file()
    assert not (public / "forbidden").exists()


def test_find_submission_rejects_sample_and_stale_outputs(tmp_path: Path) -> None:
    output = tmp_path / "output"
    workspace = output / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "sample_submission.csv").write_text("id,y\n1,0\n")
    stale = workspace / "submission.csv"
    stale.write_text("id,y\n1,1\n")
    fingerprint = {
        stale.resolve(): (
            stale.stat().st_size,
            stale.stat().st_mtime_ns,
            stale.stat().st_ino,
            __import__("hashlib").sha256(stale.read_bytes()).hexdigest(),
        )
    }
    with pytest.raises(AdapterError, match="no regular non-empty submission"):
        find_submission(output, previous=fingerprint)

    stale.unlink()
    copied_sample = workspace / "submission.csv"
    copied_sample.write_bytes((workspace / "sample_submission.csv").read_bytes())
    sample_hash = __import__("hashlib").sha256(copied_sample.read_bytes()).hexdigest()
    with pytest.raises(AdapterError, match="no regular non-empty submission"):
        find_submission(output, forbidden_hashes={sample_hash})


def test_formal_adapter_rejects_undeclared_recursive_submission(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _public_task(tmp_path)
    output = tmp_path / "run"
    request = MleLiteRequest(
        agent="ear",
        competition_id="demo",
        data_root=tmp_path,
        output_dir=output,
    )
    command = __import__("BenchmarkAdapters.contracts", fromlist=["CommandSpec"]).CommandSpec(
        argv=(
            "/bin/sh",
            "-c",
            f"mkdir -p {output / 'workspace'}; printf 'id,target\\n1,1\\n' > {output / 'workspace/submission.csv'}",
        ),
        cwd=tmp_path,
        artifact_path=output / "submission.csv",
        label="synthetic undeclared artifact",
    )
    monkeypatch.setattr(MleLiteAdapter, "build_command", lambda *_args, **_kwargs: command)
    with pytest.raises(AdapterError, match="declared final submission"):
        MleLiteAdapter("ear").run(request)


def test_native_mle_adapter_forwards_data_root(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("BenchmarkAdapters.MLEBenchLite.adapter.ROOT", tmp_path)
    public = _public_task(tmp_path)
    request = MleLiteRequest(
        agent="ear",
        competition_id="demo",
        data_root=tmp_path,
        output_dir=tmp_path / "run",
    )
    command = MleLiteAdapter("ear").build_command(request)
    assert command.env["MLE_BENCH_DATA_ROOT"] == str(tmp_path.resolve())
    assert command.env["EAR_CLI_MODE"] == "g3_legacy"
    assert command.env["EAR_AGENT_DIR"].endswith(
        "/mle-bench-agents/efficient-auto-research"
    )
    assert (request.output_dir / ".gitignore").read_text() == "*\n"
    assert public.is_dir()


def test_mlevolve_launcher_mounts_only_public_benchmark_data() -> None:
    launcher = Path("docker-eval/run_in_docker.sh").read_text(encoding="utf-8")
    mlevolve_branch = launcher.split("  MLEvolve)", 1)[1].split("    ;;", 1)[0]
    assert 'archive_tracked_source "$MLE_AGENT_DIR"' in mlevolve_branch
    assert '-v "${DATA}:${DATA}:ro"' in mlevolve_branch
    assert "MLEvolve isolation failed: private data is visible" in mlevolve_branch
    assert '-v "${EAR}:${EAR}"' not in launcher
    assert '-v "${MLE_BENCH_DATA_ROOT}:${MLE_BENCH_DATA_ROOT}:ro"' not in launcher
    assert "--network host" not in launcher
    assert "-e OPENAI_API_KEY=proxy" not in launcher
    assert 'OPENAI_API_KEY="$RELAY_API_KEY"' in launcher
    assert "MLE_SESSION_ROOT" in mlevolve_branch
    assert "agent.seed=$SEED" in mlevolve_branch


def test_arbor_launcher_archives_the_outer_benchmark_subtree() -> None:
    launcher = Path("docker-eval/run_in_docker.sh").read_text(encoding="utf-8")
    arbor_branch = launcher.split("  Arbor)", 1)[1].split("    ;;", 1)[0]
    assert 'ARBOR_SOURCE_REPO=${ARBOR_SOURCE_REPO:-$EAR}' in arbor_branch
    assert 'ARBOR_SOURCE_SUBTREE=${ARBOR_SOURCE_SUBTREE:-baselines/Arbor}' in arbor_branch
    assert 'git -C "$ARBOR_SOURCE_REPO" archive "$ARBOR_SOURCE_COMMIT:$ARBOR_SOURCE_SUBTREE"' in arbor_branch
    assert 'git -C "$ARBOR_DIR"' not in arbor_branch
    assert "src/mle/state_store.py" in arbor_branch


def test_codex_terminal_adapter_builds_real_harbor_job(tmp_path: Path) -> None:
    dataset = _terminal_dataset(tmp_path)
    request = HarborTerminalRequest(
        agent="codex",
        dataset_dir=dataset,
        jobs_dir=tmp_path / "jobs",
        task_names=("demo-task",),
        attempts=2,
        concurrency=2,
        agent_concurrency=1,
        force_build=True,
    )
    command = HarborTerminalAdapter("codex").build_command(request)
    assert command.argv[1] == "run"
    assert command.argv[command.argv.index("--agent") + 1] == "codex"
    assert command.argv[command.argv.index("--path") + 1] == str(dataset.resolve())
    assert command.argv[command.argv.index("--include-task-name") + 1] == "demo-task"
    assert "--force-build" in command.argv
    command_text = " ".join(command.argv)
    for obsolete in ("terminus-2", "run_eval.py", "dev_data", "test_data", "RepositoryAgent"):
        assert obsolete not in command_text


def test_cli_modes_keep_terminal_ao_separate_from_direct_smoke(
    tmp_path: Path,
    capsys,
) -> None:
    dataset = _terminal_dataset(tmp_path)
    assert adapter_cli_main(
        [
            "terminal-direct-smoke",
            "--agent",
            "codex",
            "--dataset-dir",
            str(dataset),
            "--jobs-dir",
            str(tmp_path / "jobs"),
            "--task",
            "demo-task",
            "--dry-run",
        ]
    ) == 0
    direct = __import__("json").loads(capsys.readouterr().out)
    assert direct["mode"] == "terminal-direct-smoke"
    assert direct["non_comparable_to_terminal_ao"] is True
    assert "harbor" in direct["command"]

    protocol = tmp_path / "ao-protocol.json"
    protocol.write_text("{}\n", encoding="utf-8")
    assert adapter_cli_main(
        [
            "terminal-ao",
            "--agent",
            "codex",
            "--protocol",
            str(protocol),
            "--output-dir",
            str(tmp_path / "ao-run"),
            "--dry-run",
        ]
    ) == 0
    ao = __import__("json").loads(capsys.readouterr().out)
    assert ao["mode"] == "terminal-ao"
    assert "BenchmarkAdapters.TerminalAO.supervisor" in ao["command"]
    assert "--upstream-base-url" in ao["command"]
    assert "--proxy" in ao["command"]
    assert " harbor run " not in f" {ao['command']} "


def test_deprecated_terminal_alias_is_marked_non_comparable(
    tmp_path: Path,
    capsys,
) -> None:
    assert adapter_cli_main(
        [
            "terminal",
            "--agent",
            "codex",
            "--dataset-dir",
            str(_terminal_dataset(tmp_path)),
            "--jobs-dir",
            str(tmp_path / "jobs"),
            "--dry-run",
        ]
    ) == 0
    payload = __import__("json").loads(capsys.readouterr().out)
    assert payload["deprecated_alias"] is True
    assert payload["non_comparable_to_terminal_ao"] is True


def test_registry_declares_distinct_mle_ao_and_direct_backends() -> None:
    assert set(AGENTS) == {
        "ear",
        "mlevolve",
        "arbor",
        "codex",
        "claude-code",
        "ml-master-2",
        "ai-scientist",
    }
    for spec in AGENTS.values():
        assert spec.mle_backend
        assert spec.terminal_ao_backend
        assert spec.terminal_direct_smoke_backend
        assert spec.terminal_ao_backend != spec.terminal_direct_smoke_backend


def test_status_and_preflight_report_two_separate_formal_protocols(capsys) -> None:
    assert adapter_cli_main(["status"]) == 0
    status = __import__("json").loads(capsys.readouterr().out)
    assert set(status["agents"]) == set(AGENTS)
    assert status["protocols"]["mle"]["task_count"] == 22
    assert status["protocols"]["terminal_ao"]["task_count"] == {
        "dev": 36,
        "held_out_test": 53,
    }
    assert status["comparison_policy"]["terminal_direct_89_is_excluded"] is True
    for agent in AGENTS:
        assert set(status["agents"][agent]["modes"]) == {"mle", "terminal-ao"}

    assert adapter_cli_main(["preflight"]) == 0
    preflight = __import__("json").loads(capsys.readouterr().out)
    assert preflight["protocols"]["mle"]["protocol_id"] == "mle-bench-lite-official-22-v1"
    assert "reconstruction" in preflight["protocols"]["terminal_ao"]["protocol_id"]


def test_cli_entrypoint_reports_expected_failures_as_json(tmp_path: Path, capsys) -> None:
    assert cli_entrypoint(
        [
            "mle",
            "--agent",
            "missing-agent",
            "--competition-id",
            "demo",
            "--data-root",
            str(tmp_path),
            "--output-dir",
            str(tmp_path / "output"),
            "--dry-run",
        ]
    ) == 1
    failure = __import__("json").loads(capsys.readouterr().out)
    assert failure["status"] == "failed"
    assert failure["score_valid"] is False
    assert failure["error_type"] == "AdapterError"


def test_terminal_dry_run_does_not_create_jobs_directory(tmp_path: Path) -> None:
    jobs_dir = tmp_path / "jobs"
    request = HarborTerminalRequest(
        agent="codex",
        dataset_dir=_terminal_dataset(tmp_path),
        jobs_dir=jobs_dir,
        dry_run=True,
    )
    HarborTerminalAdapter("codex").build_command(request)
    assert not jobs_dir.exists()


@pytest.mark.parametrize(
    ("agent", "message"),
    [
        ("ear", "native EAR"),
        ("mlevolve", "native MLEvolve"),
        ("ml-master-2", "native ML-Master"),
    ],
)
def test_unimplemented_native_terminal_agent_fails_closed(
    tmp_path: Path,
    agent: str,
    message: str,
) -> None:
    request = HarborTerminalRequest(
        agent=agent,
        dataset_dir=_terminal_dataset(tmp_path),
        jobs_dir=tmp_path / "jobs",
    )
    with pytest.raises(UnsupportedAdapterError, match=message):
        HarborTerminalAdapter(agent).build_command(request)


def test_terminal_agent_concurrency_must_not_exceed_job_concurrency(tmp_path: Path) -> None:
    request = HarborTerminalRequest(
        agent="codex",
        dataset_dir=_terminal_dataset(tmp_path),
        jobs_dir=tmp_path / "jobs",
        concurrency=1,
        agent_concurrency=2,
    )
    with pytest.raises(Exception, match="agent_concurrency"):
        HarborTerminalAdapter("codex").build_command(request)


@pytest.mark.parametrize(
    "agent_kwarg",
    [
        "api-key=do-not-leak",
        "openai-key=do-not-leak",
        "access-key=do-not-leak",
        "private-key=do-not-leak",
        "session=do-not-leak",
    ],
)
def test_terminal_agent_kwarg_rejects_secrets(
    tmp_path: Path,
    agent_kwarg: str,
) -> None:
    request = HarborTerminalRequest(
        agent="codex",
        dataset_dir=_terminal_dataset(tmp_path),
        jobs_dir=tmp_path / "jobs",
        agent_kwargs=(agent_kwarg,),
    )
    with pytest.raises(AdapterError, match="environment variables"):
        HarborTerminalAdapter("codex").build_command(request)


def test_terminal_agent_kwarg_rejects_nested_secret_name(tmp_path: Path) -> None:
    request = HarborTerminalRequest(
        agent="codex",
        dataset_dir=_terminal_dataset(tmp_path),
        jobs_dir=tmp_path / "jobs",
        agent_kwargs=('settings={"api_key":"do-not-leak"}',),
    )
    with pytest.raises(AdapterError, match="environment variables"):
        HarborTerminalAdapter("codex").build_command(request)


def test_dry_run_redacts_sensitive_argv_and_environment(tmp_path: Path) -> None:
    from BenchmarkAdapters.contracts import CommandSpec

    payload = _command_payload(
        CommandSpec(
            argv=("demo", "password=visible", "--api-key", "also-visible"),
            cwd=tmp_path,
            env={
                "Authorization": "visible",
                "NORMAL": "safe",
                "HTTPS_PROXY": "http://user:proxy-password@127.0.0.1:17892",
                "OPENAI_BASE_URL": "https://user:query-token@example.test/v1?api_key=value",
            },
        )
    )
    assert "visible" not in str(payload)
    assert payload["environment"] == {
        "HTTPS_PROXY": "http://127.0.0.1:17892",
        "NORMAL": "safe",
        "OPENAI_BASE_URL": "https://example.test/v1?api_key=%3Credacted%3E",
    }


def test_repository_output_directory_is_self_ignored(tmp_path: Path) -> None:
    from BenchmarkAdapters.contracts import protect_generated_output

    output = protect_generated_output(tmp_path / "run", tmp_path)
    assert (output / ".gitignore").read_text() == "*\n"


def test_responses_sse_synthesis_matches_codex_event_contract() -> None:
    path = Path("BenchmarkAdapters/LLMRelay/server.py")
    spec = importlib.util.spec_from_file_location("relay_proxy_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    response = {
        "id": "resp-test",
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "READY"}],
            }
        ],
        "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
    }
    stream = module._synthesize_responses_sse(response).decode("utf-8")
    assert "event: response.output_item.done" in stream
    assert "event: response.completed" in stream
    assert '"id": "resp-test"' in stream


def test_relay_forces_registered_model_reasoning_and_temperature(monkeypatch) -> None:
    monkeypatch.setenv("LLM_FORCE_MODEL", "gpt-5.5")
    monkeypatch.setenv(
        "LLM_FORCE_PARAMETERS_JSON",
        '{"reasoning_effort":"high","temperature":1.0}',
    )
    path = Path("BenchmarkAdapters/LLMRelay/server.py")
    spec = importlib.util.spec_from_file_location("relay_proxy_fairness_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    rewritten = module._rewrite_body(
        {
            "model": "different-model",
            "reasoning_effort": "low",
            "temperature": 0.2,
            "messages": [{"role": "user", "content": "test"}],
        },
        "/chat/completions",
    )
    assert rewritten["model"] == "gpt-5.5"
    assert rewritten["reasoning_effort"] == "high"
    assert rewritten["temperature"] == 1.0
