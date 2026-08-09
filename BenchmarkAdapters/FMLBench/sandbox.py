"""Filesystem isolation for formal FML Agent processes."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from ..contracts import AdapterError, CommandSpec
from ..registry import AGENTS, ROOT


def _parents(argv: list[str], target: Path, created: set[Path]) -> None:
    current = Path("/")
    for part in target.parent.parts[1:]:
        current /= part
        if current in created or current in {Path("/usr"), Path("/bin"), Path("/lib"), Path("/lib64")}:
            continue
        argv.extend(("--dir", str(current)))
        created.add(current)


def _bind(
    argv: list[str], source: Path, target: Path, created: set[Path], *, read_only: bool
) -> None:
    source = source.resolve()
    if not source.exists():
        raise AdapterError(f"FML sandbox mount is missing: {source}")
    _parents(argv, target, created)
    argv.extend(("--ro-bind" if read_only else "--bind", str(source), str(target)))


def _runtime_root(executable: Path) -> Path:
    resolved = executable.resolve()
    for parent in (resolved, *resolved.parents):
        if parent.name.startswith("node-v"):
            return parent
    if "bin" in resolved.parts:
        return resolved.parent.parent
    return resolved.parent


def sandbox_fml_command(
    *,
    agent_id: str,
    command: CommandSpec,
    workspace: Path,
    agent_output_dir: Path,
    development_socket: Path,
    relay_socket: Path,
    editable_paths: tuple[str, ...],
) -> CommandSpec:
    if agent_id not in AGENTS:
        raise AdapterError(f"unknown FML Agent: {agent_id}")
    bubblewrap = Path(shutil.which("bwrap") or "")
    if not bubblewrap.is_file():
        raise AdapterError("formal FML Agent execution requires Bubblewrap")
    executable = Path(command.argv[0]).resolve()
    if not executable.is_file():
        raise AdapterError(f"FML native executable is missing: {executable}")
    workspace = workspace.resolve()
    agent_output_dir = agent_output_dir.resolve()
    development_socket = development_socket.resolve()
    relay_socket = relay_socket.resolve()
    argv = [
        str(bubblewrap),
        "--die-with-parent",
        "--new-session",
        "--unshare-all",
        "--ro-bind",
        "/usr",
        "/usr",
        "--ro-bind",
        "/bin",
        "/bin",
        "--ro-bind",
        "/lib",
        "/lib",
        "--ro-bind",
        "/lib64",
        "/lib64",
        "--ro-bind",
        "/etc/ssl",
        "/etc/ssl",
        "--ro-bind",
        "/etc/resolv.conf",
        "/etc/resolv.conf",
        "--ro-bind",
        "/etc/hosts",
        "/etc/hosts",
        "--ro-bind",
        "/etc/passwd",
        "/etc/passwd",
        "--ro-bind",
        "/etc/group",
        "/etc/group",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--tmpfs",
        "/tmp",
        "--dir",
        "/tmp/home",
        "--dir",
        "/tmp/codex-home",
        "--dir",
        "/tmp/xdg-cache",
        "--dir",
        "/tmp/xdg-config",
        "--dir",
        "/capability",
        "--dir",
        "/relay",
    ]
    created = {
        Path("/tmp"),
        Path("/tmp/home"),
        Path("/tmp/codex-home"),
        Path("/tmp/xdg-cache"),
        Path("/tmp/xdg-config"),
        Path("/capability"),
        Path("/relay"),
    }
    _bind(argv, workspace, workspace, created, read_only=True)
    git_dir = workspace / ".git"
    if git_dir.is_dir():
        _bind(argv, git_dir, git_dir, created, read_only=False)
    for relative in editable_paths:
        editable = workspace / relative
        _bind(argv, editable, editable, created, read_only=False)
    _bind(argv, agent_output_dir, agent_output_dir, created, read_only=False)
    _bind(argv, ROOT / "BenchmarkAdapters", ROOT / "BenchmarkAdapters", created, read_only=True)
    _bind(argv, AGENTS[agent_id].install_path, AGENTS[agent_id].install_path, created, read_only=True)
    runtime = _runtime_root(executable)
    if runtime not in {Path("/"), Path("/usr"), Path("/bin"), Path("/lib"), Path("/lib64")}:
        _bind(argv, runtime, runtime, created, read_only=True)
    _bind(argv, development_socket, Path("/capability/dev.sock"), created, read_only=False)
    _bind(argv, relay_socket, Path("/relay/llm.sock"), created, read_only=False)
    child_argv = tuple(
        value.replace(str(development_socket), "/capability/dev.sock")
        for value in command.argv
    )
    environment = {
        key: value.replace(str(development_socket), "/capability/dev.sock")
        for key, value in command.env.items()
    }
    environment.update(
        {
            "HOME": "/tmp/home",
            "CODEX_HOME": "/tmp/codex-home",
            "XDG_CACHE_HOME": "/tmp/xdg-cache",
            "XDG_CONFIG_HOME": "/tmp/xdg-config",
            "TMPDIR": "/tmp",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "OPENAI_BASE_URL": "http://127.0.0.1:6200/v1",
            "OPENAI_API_BASE": "http://127.0.0.1:6200/v1",
            "GPT_BASE_URL": "http://127.0.0.1:6200/v1",
            "ANTHROPIC_BASE_URL": "http://127.0.0.1:6200",
            "OPENAI_API_KEY": "proxy",
            "UPSTREAM_API_KEY": "proxy",
            "ANTHROPIC_API_KEY": "proxy",
            "NO_PROXY": "localhost,127.0.0.1",
            "no_proxy": "localhost,127.0.0.1",
        }
    )
    argv.extend(
        (
            "--chdir",
            str(workspace),
            "--",
            "/usr/bin/python3",
            str(ROOT / "BenchmarkAdapters/unix_relay_forwarder.py"),
            "--socket",
            "/relay/llm.sock",
            "--port",
            "6200",
            "--",
            *child_argv,
        )
    )
    return CommandSpec(
        argv=tuple(argv),
        cwd=workspace,
        env=environment,
        timeout_seconds=command.timeout_seconds,
        label=f"{command.label} (filesystem isolated)",
        inherit_env=False,
    )


__all__ = ["sandbox_fml_command"]
