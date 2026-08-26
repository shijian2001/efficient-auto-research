"""Filesystem-isolated execution for Terminal AO native launchers."""

from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path

from ...contracts import AdapterError, CommandSpec, require_directory, require_file
from ...registry import AGENTS, ROOT


def _resolved_python_root(python: Path) -> Path:
    executable = python.resolve()
    if not executable.is_file():
        raise AdapterError(f"sandbox Python executable does not exist: {python}")
    return executable.parents[1]


def _python_runtime_mounts(python: Path) -> tuple[Path, ...]:
    mounts = [python.parent.parent, _resolved_python_root(python)]
    if python.is_symlink():
        target = Path(os.readlink(python))
        if not target.is_absolute():
            target = python.parent / target
        mounts.append(target.absolute().parent.parent)
    return tuple(dict.fromkeys(path.absolute() for path in mounts))


def _mount_parent_dirs(argv: list[str], target: Path, created: set[Path]) -> None:
    current = Path("/")
    for part in target.parent.parts[1:]:
        current /= part
        if current in created or current in {Path("/usr"), Path("/bin"), Path("/lib"), Path("/lib64")}:
            continue
        argv.extend(["--dir", str(current)])
        created.add(current)


def _ro_bind(argv: list[str], source: Path, target: Path, created: set[Path]) -> None:
    source = source.resolve()
    _mount_parent_dirs(argv, target, created)
    argv.extend(["--ro-bind", str(source), str(target)])


def _bind(argv: list[str], source: Path, target: Path, created: set[Path]) -> None:
    source = source.resolve()
    _mount_parent_dirs(argv, target, created)
    argv.extend(["--bind", str(source), str(target)])


def _runtime_mounts(agent: str) -> tuple[Path, ...]:
    adapter_venv = require_directory(ROOT / "BenchmarkAdapters/.venv", "adapter environment")
    mounts = [ROOT / "BenchmarkAdapters", *_python_runtime_mounts(adapter_venv / "bin/python")]
    if agent == "ear":
        venv = ROOT / "BenchmarkAdapters/environments/mle/ear/.venv"
        mounts.extend((AGENTS[agent].install_path, *_python_runtime_mounts(venv / "bin/python")))
    elif agent in {"ai-scientist", "arbor"}:
        venv = AGENTS[agent].execution_path / ".venv"
        mounts.extend((AGENTS[agent].install_path, *_python_runtime_mounts(venv / "bin/python")))
    return tuple(dict.fromkeys(path.absolute() for path in mounts))


def _require_socket(path: Path, description: str) -> Path:
    resolved = path.resolve()
    if not resolved.exists() or not stat.S_ISSOCK(resolved.stat().st_mode):
        raise AdapterError(f"{description} does not exist: {resolved}")
    return resolved


def sandbox_native_ao_command(
    *,
    agent: str,
    command: CommandSpec,
    candidate_dir: Path,
    launcher_output_dir: Path,
    host_dev_socket: Path,
    host_relay_socket: Path,
    resolver_path: Path,
) -> CommandSpec:
    """Wrap a native command without mounting protocol, split, dataset, or repository root."""
    if agent not in AGENTS:
        raise AdapterError(f"unknown baseline agent: {agent}")
    bwrap = require_file(Path(shutil.which("bwrap") or ""), "Bubblewrap executable")
    sandbox_runner = require_file(ROOT / "BenchmarkAdapters/sandbox_runner.py", "sandbox runner")
    relay_forwarder = require_file(
        ROOT / "BenchmarkAdapters/LLMRelay/forwarder.py", "sandbox relay forwarder"
    )
    adapter_python = require_file(ROOT / "BenchmarkAdapters/.venv/bin/python", "adapter Python")
    candidate_dir = require_directory(candidate_dir, "Terminal AO candidate")
    launcher_output_dir.mkdir(parents=True, exist_ok=True)
    host_dev_socket = _require_socket(host_dev_socket, "dev broker socket")
    host_relay_socket = _require_socket(host_relay_socket, "LLM relay socket")
    resolver_path = require_file(resolver_path, "sandbox resolver")
    codex_home = launcher_output_dir / "codex-home"
    codex_home.mkdir(parents=True, exist_ok=True)
    auth_path = codex_home / "auth.json"
    if not auth_path.exists():
        auth_path.write_text('{"OPENAI_API_KEY":"proxy"}\n', encoding="utf-8")
        auth_path.chmod(0o600)

    argv = [
        str(bwrap),
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
        str(resolver_path),
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
        "/agent-bin",
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
        Path("/agent-bin"),
        Path("/capability"),
        Path("/relay"),
    }
    source_path = AGENTS[agent].install_path.absolute()
    execution_path = AGENTS[agent].execution_path.absolute()
    for mount in _runtime_mounts(agent):
        _ro_bind(argv, mount, mount, created)
        if mount == source_path and execution_path != source_path:
            _ro_bind(argv, source_path, execution_path, created)
    _bind(argv, candidate_dir, candidate_dir, created)
    _bind(argv, launcher_output_dir, launcher_output_dir.resolve(), created)
    _bind(argv, host_dev_socket, Path("/capability/dev.sock"), created)
    _bind(argv, host_relay_socket, Path("/relay/llm.sock"), created)
    _bind(argv, codex_home, Path("/tmp/codex-home"), created)

    child_argv = [
        argument.replace(str(host_dev_socket), "/capability/dev.sock")
        for argument in command.argv
    ]
    if agent == "codex":
        executable = require_file(Path(command.argv[0]), "Codex executable")
        _ro_bind(argv, executable, Path("/agent-bin/codex"), created)
        child_argv[0] = "/agent-bin/codex"
    elif agent == "claude-code":
        executable = require_file(Path(command.argv[0]), "Claude Code executable")
        _ro_bind(argv, executable, Path("/agent-bin/claude"), created)
        child_argv[0] = "/agent-bin/claude"

    argv.extend(
        [
            "--chdir",
            str(command.cwd.resolve()),
            str(adapter_python),
            str(relay_forwarder),
            "--socket",
            "/relay/llm.sock",
            "--port",
            "6200",
            "--",
            *child_argv,
        ]
    )
    environment = {
        "HOME": "/tmp/home",
        "CODEX_HOME": "/tmp/codex-home",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/agent-bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "TMPDIR": "/tmp",
        "XDG_CACHE_HOME": "/tmp/xdg-cache",
        "XDG_CONFIG_HOME": "/tmp/xdg-config",
        "OPENAI_BASE_URL": "http://127.0.0.1:6200/v1",
        "OPENAI_API_BASE": "http://127.0.0.1:6200/v1",
        "GPT_BASE_URL": "http://127.0.0.1:6200/v1",
        "ANTHROPIC_BASE_URL": "http://127.0.0.1:6200",
        "OPENAI_API_KEY": "proxy",
        "ANTHROPIC_API_KEY": "proxy",
        "NO_PROXY": "localhost,127.0.0.1",
        "no_proxy": "localhost,127.0.0.1",
    }
    environment.update(
        {
            key: value.replace(str(host_dev_socket), "/capability/dev.sock")
            for key, value in command.env.items()
        }
    )
    wrapped = (
        str(adapter_python),
        str(sandbox_runner),
        "--",
        *argv,
    )
    return CommandSpec(
        argv=wrapped,
        cwd=ROOT,
        env=environment,
        timeout_seconds=command.timeout_seconds,
        label=f"{command.label} (filesystem isolated)",
        inherit_env=False,
    )


__all__ = ["sandbox_native_ao_command"]
