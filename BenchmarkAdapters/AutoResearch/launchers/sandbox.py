"""Filesystem isolation for native Autoresearch outer Agent processes."""

from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path

from ...contracts import AdapterError, CommandSpec
from ...registry import AGENTS


def _parent_dirs(argv: list[str], target: Path, created: set[Path]) -> None:
    current = Path("/")
    for part in target.parent.parts[1:]:
        current /= part
        if current in created or current in {Path("/usr"), Path("/bin"), Path("/lib"), Path("/lib64")}:
            continue
        argv.extend(("--dir", str(current)))
        created.add(current)


def _bind(
    argv: list[str],
    source: Path,
    target: Path,
    created: set[Path],
    *,
    read_only: bool,
) -> None:
    source = source.resolve()
    if not source.exists():
        raise AdapterError(f"Autoresearch sandbox mount is missing: {source}")
    _parent_dirs(argv, target, created)
    argv.extend(("--ro-bind" if read_only else "--bind", str(source), str(target)))


def _socket(path: Path) -> Path:
    path = path.resolve()
    if not path.exists() or not stat.S_ISSOCK(path.stat().st_mode):
        raise AdapterError(f"Autoresearch dev broker socket is missing: {path}")
    return path


def _node_distribution(executable: Path) -> Path | None:
    resolved = executable.resolve()
    for parent in (resolved, *resolved.parents):
        if parent.name.startswith("node-v"):
            return parent
    return None


def sandbox_native_command(
    *,
    agent: str,
    command: CommandSpec,
    workspace: Path,
    output_dir: Path,
    runtime_root: Path,
    host_socket: Path,
    host_relay_socket: Path,
) -> CommandSpec:
    if agent not in AGENTS:
        raise AdapterError(f"unknown Autoresearch Agent: {agent}")
    bubblewrap = Path(shutil.which("bwrap") or "")
    if not bubblewrap.is_file():
        raise AdapterError("Autoresearch formal Agent execution requires bubblewrap")
    workspace = workspace.resolve()
    runtime_root = runtime_root.resolve()
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    host_socket = _socket(host_socket)
    host_relay_socket = _socket(host_relay_socket)
    argv = [
        str(bubblewrap),
        "--die-with-parent",
        "--new-session",
        "--unshare-ipc",
        "--unshare-pid",
        "--unshare-uts",
        "--unshare-net",
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
        Path("/tmp/xdg-cache"),
        Path("/tmp/xdg-config"),
        Path("/capability"),
        Path("/relay"),
    }
    _bind(argv, workspace, workspace, created, read_only=False)
    _bind(argv, output_dir, output_dir, created, read_only=False)
    _bind(argv, runtime_root, runtime_root, created, read_only=True)
    _bind(argv, AGENTS[agent].install_path, AGENTS[agent].install_path, created, read_only=True)
    executable = Path(command.argv[0])
    node_distribution = _node_distribution(executable)
    if node_distribution is not None:
        _bind(argv, node_distribution, node_distribution, created, read_only=True)
    elif executable.is_absolute():
        original_runtime = executable.parent.parent.resolve()
        if original_runtime not in {
            Path("/"),
            Path("/usr"),
            Path("/bin"),
            Path("/lib"),
            Path("/lib64"),
        }:
            _bind(argv, original_runtime, original_runtime, created, read_only=True)
        resolved = executable.resolve()
        if AGENTS[agent].install_path.resolve() not in (resolved, *resolved.parents):
            runtime = resolved.parents[1]
            if runtime not in {Path("/usr"), Path("/bin"), Path("/lib"), Path("/lib64")}:
                _bind(argv, runtime, runtime, created, read_only=True)
    _bind(argv, host_socket, Path("/capability/dev.sock"), created, read_only=False)
    _bind(argv, host_relay_socket, Path("/relay/llm.sock"), created, read_only=False)
    child_argv = tuple(
        value.replace(str(host_socket), "/capability/dev.sock") for value in command.argv
    )
    path_entries = ["/usr/local/sbin", "/usr/local/bin", "/usr/sbin", "/usr/bin", "/sbin", "/bin"]
    if node_distribution is not None:
        path_entries.insert(0, str(node_distribution / "bin"))
    environment = {
        "HOME": "/tmp/home",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": ":".join(path_entries),
        "TMPDIR": "/tmp",
        "XDG_CACHE_HOME": "/tmp/xdg-cache",
        "XDG_CONFIG_HOME": "/tmp/xdg-config",
    }
    allowed_host_environment = {
        "AUTORESEARCH_PROPOSER_COMMAND",
        "AUTORESEARCH_ARBOR_PROVIDER_FACTORY",
        "AUTORESEARCH_CODEX_BASE_URL",
        "AUTORESEARCH_EVOMASTER_LLM_FACTORY",
        "AUTORESEARCH_AISCIENTIST_LLM_FACTORY",
        "AUTORESEARCH_MODEL",
        "AUTORESEARCH_MODEL_PARAMETERS",
        "AUTORESEARCH_REQUEST_TIMEOUT_SECONDS",
        "AUTORESEARCH_RETRY_POLICY",
        "AUTORESEARCH_MODEL_FACTORY_ROOT",
        "GPT_CHAT_MODEL",
        "MODEL",
        "OPTIMIZATION_ARTIFACT_NAME",
        "OPTIMIZATION_METRIC_DIRECTION",
        "OPTIMIZATION_METRIC_NAME",
        "OPTIMIZATION_NATIVE_COMPONENT",
        "OPTIMIZATION_PROGRAM_NAME",
        "OPTIMIZATION_STATE_NAME",
        "OPTIMIZATION_TASK_INSTRUCTION",
        "OPTIMIZATION_TASK_NAME",
        "BENCHMARK_TASK_SPEC_SHA256",
        "BENCHMARK_TASK_SPEC_TEXT",
    }
    environment.update({key: value for key, value in os.environ.items() if key in allowed_host_environment})
    environment.update(
        {
            key: value.replace(str(host_socket), "/capability/dev.sock")
            for key, value in command.env.items()
        }
    )
    argv.extend(
        (
            "--chdir",
            str(workspace),
            "--",
            "/usr/bin/python3",
            str(runtime_root / "BenchmarkAdapters/LLMRelay/forwarder.py"),
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


__all__ = ["sandbox_native_command"]
