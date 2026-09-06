from pathlib import Path
import subprocess

import pytest

from BenchmarkAdapters.contracts import AdapterError
from BenchmarkAdapters.MLEBenchLite import network
from BenchmarkAdapters.MLEBenchLite.adapter import (
    MleLiteRequest,
    MleLiteWorkspace,
    _ai_scientist_command,
    _ml_master_command,
    _workspace_command,
)


@pytest.mark.parametrize("agent", ["codex", "claude-code", "ml-master-2", "ai-scientist"])
def test_native_agents_preserve_download_proxy_and_local_relay(tmp_path, monkeypatch, agent):
    monkeypatch.setattr(
        "BenchmarkAdapters.MLEBenchLite.adapter.require_clean_upstream_source", lambda _: None,
    )
    public = tmp_path / "demo/prepared/public"
    public.mkdir(parents=True)
    (public / "description.md").write_text("demo")
    config = tmp_path / "config.yaml"
    config.write_text("{}")
    proxy = "http://172.17.0.1:32145"
    request = MleLiteRequest(
        agent=agent, competition_id="demo", data_root=tmp_path, output_dir=tmp_path / "run",
        model="test-model", dry_run=True, config_path=config, download_proxy=proxy,
        agent_variant=f"{agent}-budget-loop" if agent in {"codex", "claude-code"} else "default",
    )
    if agent in {"codex", "claude-code"}:
        workspace = MleLiteWorkspace("demo", public, tmp_path / "workspace", public / "description.md", public / "sample.csv")
        command = _workspace_command(request, workspace, preview=True)
    elif agent == "ml-master-2":
        command = _ml_master_command(request)
    else:
        command = _ai_scientist_command(request)
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        assert command.env[key] == proxy
    assert "127.0.0.1" in command.env["NO_PROXY"].split(",")
    assert command.env["OPENAI_BASE_URL"] == "http://127.0.0.1:6200/v1"
    assert command.inherit_env is False


def test_upstream_proxy_exempts_only_local_relay():
    assert network.upstream_proxy("http://127.0.0.1:6201/v1") == ""
    assert network.upstream_proxy("http://localhost:6201/v1") == ""
    assert network.upstream_proxy("https://api.example.com/v1") == network.MLE_PROXY


def test_unavailable_proxy_fails_closed(monkeypatch):
    def unavailable(*args, **kwargs):
        raise ConnectionRefusedError()
    monkeypatch.setattr(network.socket, "create_connection", unavailable)
    with pytest.raises(AdapterError, match="required MLE proxy is unavailable"):
        network.check_proxy()


def test_missing_image_is_pulled_through_proxy_then_loaded(tmp_path, monkeypatch):
    monkeypatch.setattr(network, "ROOT", tmp_path)
    monkeypatch.setattr(network, "check_proxy", lambda: None)
    crane = tmp_path / "cache/tools/crane"
    crane.parent.mkdir(parents=True)
    crane.touch()
    calls = []
    loaded = False

    def run(argv, **kwargs):
        nonlocal loaded
        calls.append((argv, kwargs))
        if argv[:3] == ["docker", "image", "inspect"]:
            return subprocess.CompletedProcess(argv, 0 if loaded else 1)
        if argv[:2] == ["docker", "load"]:
            loaded = True
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(network.subprocess, "run", run)
    network.ensure_image("example/image:test")
    pull, kwargs = next((argv, kw) for argv, kw in calls if argv[0] == str(crane))
    assert pull[1:5] == ["pull", "--platform", "linux/amd64", "example/image:test"]
    assert kwargs["env"]["HTTPS_PROXY"] == network.MLE_PROXY
    assert loaded
    assert all(argv[:2] != ["docker", "pull"] for argv, _ in calls)
    calls.clear()
    network.ensure_image("example/image:test")
    assert len(calls) == 1
