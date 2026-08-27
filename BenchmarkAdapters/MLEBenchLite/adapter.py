"""Shared MLE-Bench Lite adapter contract."""

from __future__ import annotations

import os
import csv
import io
import json
import shutil
import subprocess
import textwrap
import zipfile
import hashlib
import tempfile
import stat
from decimal import Decimal, InvalidOperation
from dataclasses import dataclass, field
from dataclasses import replace
from pathlib import Path

from ..contracts import (
    AdapterError,
    CommandSpec,
    UnsupportedAdapterError,
    protect_generated_output,
    require_directory,
    require_file,
)
from ..process import DEFAULT_PROXY, relay_client_env, run_command
from ..LLMRelay import RelayProcess
from ..registry import AGENTS, ROOT
from ..task_specs import task_spec_digest, task_spec_text
from ..thin_registry import require_clean_upstream_source, require_thin_support, selected_variant


@dataclass(frozen=True)
class MleLiteRequest:
    agent: str
    competition_id: str
    data_root: Path
    output_dir: Path
    gpu_id: int = 0
    steps: int = 1
    timeout_seconds: int = 900
    model: str | None = None
    upstream_base_url: str = ""
    proxy: str = DEFAULT_PROXY
    run_tag: str | None = None
    instruction: str | None = None
    max_turns: int = 8
    config_path: Path | None = None
    force: bool = False
    dry_run: bool = False
    relay_socket: Path | None = None
    seed: int = 0
    model_parameters: dict[str, object] = field(default_factory=dict)
    request_timeout_seconds: int | None = None
    retry_policy: dict[str, object] = field(default_factory=dict)
    agent_variant: str = "default"
    runtime_image: str | None = None
    image_pull_policy: str | None = None
    official_llm_profile: str | None = None


@dataclass(frozen=True)
class MleLiteWorkspace:
    competition_id: str
    public_dir: Path
    workspace_dir: Path
    description_path: Path
    sample_submission_path: Path


def _file_fingerprint(path: Path) -> tuple[int, int, int, str] | None:
    if not path.is_file() or path.is_symlink():
        return None
    stat = path.stat()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return stat.st_size, stat.st_mtime_ns, stat.st_ino, digest


def _payload_signatures(data: bytes) -> set[str]:
    signatures = {hashlib.sha256(data).hexdigest()}
    try:
        rows = []
        for row in csv.reader(io.StringIO(data.decode("utf-8-sig"), newline="")):
            normalized = []
            for cell in row:
                value = cell.strip()
                try:
                    number = Decimal(value)
                    if not number.is_finite():
                        raise InvalidOperation
                    if number == 0:
                        number = Decimal(0)
                    normalized.append(["number", str(number.normalize())])
                except (InvalidOperation, ValueError):
                    normalized.append(["text", value])
            rows.append(normalized)
    except (UnicodeDecodeError, csv.Error):
        return signatures
    canonical = json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode()
    signatures.add("csv:" + hashlib.sha256(canonical).hexdigest())
    return signatures


def _file_signatures(path: Path) -> set[str]:
    if not path.is_file() or path.is_symlink():
        return set()
    return _payload_signatures(path.read_bytes())


def _submission_candidates(output_dir: Path) -> list[Path]:
    candidates = [output_dir / "submission.csv", output_dir / "workspace/submission.csv"]
    candidates.extend(sorted(output_dir.rglob("submission.csv")))
    return list(dict.fromkeys(candidates))


def _submission_snapshot(*output_dirs: Path) -> dict[Path, tuple[int, int, int, str]]:
    snapshot: dict[Path, tuple[int, int, int, str]] = {}
    for output_dir in output_dirs:
        for candidate in _submission_candidates(output_dir):
            fingerprint = _file_fingerprint(candidate)
            if fingerprint is not None:
                snapshot[candidate.resolve()] = fingerprint
    return snapshot


def _sample_hashes(request: MleLiteRequest) -> set[str]:
    public_dir = public_task_dir(request)
    hashes: set[str] = set()
    for path in public_dir.rglob("*.csv"):
        if "sample" in path.name.lower() or "submission" in path.name.lower():
            fingerprint = _file_fingerprint(path)
            if fingerprint is not None:
                hashes.update(_file_signatures(path))
    for archive_path in public_dir.rglob("*.zip"):
        if "sample" not in archive_path.name.lower() and "submission" not in archive_path.name.lower():
            continue
        with zipfile.ZipFile(archive_path) as archive:
            for member in archive.namelist():
                if member.lower().endswith(".csv"):
                    hashes.update(_payload_signatures(archive.read(member)))
    return hashes


NATIVE_DOCKER_NAMES = {
    "ear": "efficient-auto-research",
    "mlevolve": "MLEvolve",
    "arbor": "Arbor",
}


def _required_executable(name: str) -> Path:
    executable = shutil.which(name)
    if executable is None:
        raise AdapterError(f"required executable is not installed: {name}")
    return Path(executable).resolve()


def _minimal_host_environment() -> dict[str, str]:
    allowed = {
        "DOCKER_HOST",
        "LANG",
        "LC_ALL",
        "PATH",
        "SSL_CERT_DIR",
        "SSL_CERT_FILE",
        "XDG_RUNTIME_DIR",
    }
    environment = {key: value for key, value in os.environ.items() if key in allowed}
    environment.setdefault("PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")
    environment.setdefault("LANG", "C.UTF-8")
    environment.setdefault("LC_ALL", "C.UTF-8")
    return environment


def _host_relay_environment(request: MleLiteRequest) -> dict[str, str]:
    environment = _minimal_host_environment()
    credential = os.environ.get("UPSTREAM_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not credential and not request.dry_run:
        raise AdapterError("MLE formal launcher requires UPSTREAM_API_KEY or OPENAI_API_KEY")
    if credential:
        environment["UPSTREAM_API_KEY"] = credential
    return environment


def _mount_parent_directories(argv: list[str], path: Path, created: set[Path]) -> None:
    current = Path("/")
    for part in path.absolute().parent.parts[1:]:
        current /= part
        if current not in created:
            argv.extend(["--dir", str(current)])
            created.add(current)


def _python_runtime_roots(executable: Path) -> tuple[Path, ...]:
    roots: list[Path] = []
    if executable.is_symlink():
        link = Path(os.readlink(executable))
        if not link.is_absolute():
            link = executable.parent / link
        roots.append(link.absolute().parent.parent)
    roots.append(executable.resolve().parents[1])
    return tuple(dict.fromkeys(roots))


def _native_host_sandbox_argv(
    *,
    request: MleLiteRequest,
    wrapper_argv: tuple[str, ...],
    source_root: Path,
    public_dir: Path,
    docker_socket: Path | None = None,
) -> tuple[str, ...]:
    if request.relay_socket is None:
        raise AdapterError("native MLE sandbox requires a host-owned Unix relay socket")
    bwrap = _required_executable("bwrap")
    sandbox_runner = require_file(
        ROOT / "BenchmarkAdapters/sandbox_runner.py", "sandbox network runner"
    )
    relay_forwarder = require_file(
        ROOT / "BenchmarkAdapters/LLMRelay/forwarder.py", "sandbox relay forwarder"
    )
    adapter_venv = require_directory(ROOT / "BenchmarkAdapters/.venv", "adapter runtime")
    output_dir = request.output_dir.resolve()
    if not request.dry_run:
        for name in ("home", "tmp", "cache", "config"):
            (output_dir / name).mkdir(parents=True, exist_ok=True)
    mounts = (
        (source_root.resolve(), False),
        (public_dir.resolve(), False),
        (output_dir, True),
        ((ROOT / "BenchmarkAdapters").resolve(), False),
        (adapter_venv.resolve(), False),
        *tuple(
            (runtime_root, False)
            for runtime_root in _python_runtime_roots(adapter_venv / "bin/python")
        ),
    )
    argv = [
        str(adapter_venv / "bin/python"),
        str(sandbox_runner),
        "--",
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
    ]
    created: set[Path] = set()
    for path, writable in mounts:
        _mount_parent_directories(argv, path, created)
        argv.extend(["--bind" if writable else "--ro-bind", str(path), str(path)])
    runtime_paths: set[Path] = set()
    for value in wrapper_argv:
        executable = Path(value)
        if (
            not executable.is_absolute()
            or not executable.is_file()
            or not os.access(executable, os.X_OK)
        ):
            continue
        venv_root = next(
            (parent for parent in executable.parents if parent.name == ".venv"),
            None,
        )
        if venv_root is not None:
            resolved_venv = venv_root.resolve()
            if (
                resolved_venv not in runtime_paths
                and not resolved_venv.is_relative_to(source_root.resolve())
            ):
                _mount_parent_directories(argv, resolved_venv, created)
                argv.extend(["--ro-bind", str(resolved_venv), str(resolved_venv)])
                runtime_paths.add(resolved_venv)
            for python_name in ("python", "python3"):
                python_executable = resolved_venv / "bin" / python_name
                if not python_executable.exists():
                    continue
                for runtime_root in _python_runtime_roots(python_executable):
                    if runtime_root in runtime_paths:
                        continue
                    _mount_parent_directories(argv, runtime_root, created)
                    argv.extend(["--ro-bind", str(runtime_root), str(runtime_root)])
                    runtime_paths.add(runtime_root)
        resolved = executable.resolve()
        for runtime_root in _python_runtime_roots(executable):
            if runtime_root in runtime_paths or runtime_root.is_relative_to(
                source_root.resolve()
            ):
                continue
            _mount_parent_directories(argv, runtime_root, created)
            argv.extend(["--ro-bind", str(runtime_root), str(runtime_root)])
            runtime_paths.add(runtime_root)
    relay_socket = request.relay_socket.resolve()
    _mount_parent_directories(argv, relay_socket, created)
    argv.extend(["--ro-bind", str(relay_socket), str(relay_socket)])
    if docker_socket is not None:
        docker_socket = docker_socket.resolve()
        if not docker_socket.exists():
            raise AdapterError(f"Docker socket does not exist: {docker_socket}")
        _mount_parent_directories(argv, docker_socket, created)
        argv.extend(["--bind", str(docker_socket), str(docker_socket)])
    minor_result = subprocess.run(
        ["nvidia-smi", "-q", "-i", str(request.gpu_id)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    minor = next(
        (
            line.split(":", 1)[1].strip()
            for line in minor_result.stdout.splitlines()
            if "Minor Number" in line and ":" in line
        ),
        "",
    )
    if minor_result.returncode or not minor.isdigit():
        raise AdapterError(f"could not resolve GPU device minor for GPU {request.gpu_id}")
    for device in (
        Path(f"/dev/nvidia{minor}"),
        Path("/dev/nvidiactl"),
        Path("/dev/nvidia-uvm"),
        Path("/dev/nvidia-uvm-tools"),
    ):
        if device.exists():
            argv.extend(["--dev-bind", str(device), str(device)])
    argv.extend(
        [
            "--chdir",
            str(source_root.resolve()),
            str(adapter_venv / "bin/python"),
            str(relay_forwarder),
            "--socket",
            str(relay_socket),
            "--port",
            "6200",
            "--",
            *wrapper_argv,
        ]
    )
    return tuple(argv)


def _workspace_sandbox_argv(
    executable: Path,
    workspace: MleLiteWorkspace,
    command: tuple[str, ...],
    *,
    relay_socket: Path,
    gpu_id: int | None = None,
) -> tuple[str, ...]:
    bwrap = _required_executable("bwrap")
    benchmark_venv = require_directory(
        ROOT / "mle-bench-lite/.venv",
        "MLE-Bench Lite UV environment",
    )
    benchmark_python = require_file(
        benchmark_venv / "bin/python",
        "MLE-Bench Lite Python executable",
    ).resolve()
    benchmark_python_home = benchmark_python.parents[1]
    benchmark_python_link = Path(os.readlink(benchmark_venv / "bin/python"))
    benchmark_python_link_home = benchmark_python_link.parents[1]
    relay_forwarder = require_file(
        ROOT / "BenchmarkAdapters/LLMRelay/forwarder.py",
        "sandbox relay forwarder",
    )
    sandbox_runner = require_file(
        ROOT / "BenchmarkAdapters/sandbox_runner.py",
        "sandbox network runner",
    )
    if workspace.workspace_dir.exists() is False:
        raise AdapterError(f"MLE workspace does not exist: {workspace.workspace_dir}")
    if workspace.public_dir.exists() is False:
        raise AdapterError(f"MLE public task does not exist: {workspace.public_dir}")
    relay_socket = relay_socket.resolve()
    if not relay_socket.exists() or not stat.S_ISSOCK(relay_socket.stat().st_mode):
        raise AdapterError(f"sandbox relay socket does not exist: {relay_socket}")
    resolver_path = relay_socket.parent / "resolv.conf"
    resolver_path.write_text("nameserver 10.0.2.3\n", encoding="utf-8")
    codex_home = workspace.workspace_dir.parent / "codex-home"
    codex_home.mkdir(parents=True, exist_ok=True)
    auth_path = codex_home / "auth.json"
    if not auth_path.exists():
        auth_path.write_text('{"OPENAI_API_KEY":"proxy"}\n', encoding="utf-8")
        auth_path.chmod(0o600)
    sandbox_executable = f"/agent-bin/{executable.name}"
    # Some CLIs resolve companion executables as siblings of argv[0]: Codex needs
    # codex-code-mode-host next to the binary, and without it the agent starts,
    # silently loses its shell tool, and returns "no executable tool has been
    # exposed to me" instead of a submission. Bind the whole shipping directory
    # when the resolved target really is that CLI's own bin dir. Claude Code
    # instead resolves to a version-named file inside a shared versions/
    # directory; binding that parent would expose every installed version and
    # would not even land on /agent-bin/claude, so that case keeps the single
    # file bind.
    resolved_executable = executable.resolve()
    executable_dir = resolved_executable.parent
    bind_executable_dir = resolved_executable.name == executable.name and any(
        sibling.name != executable.name and sibling.name.startswith(executable.name + "-")
        for sibling in executable_dir.iterdir()
        if sibling.is_file()
    )
    argv = [
        str(benchmark_venv / "bin/python"),
        str(sandbox_runner),
        "--",
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
        "--ro-bind",
        "/sys",
        "/sys",
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
        "/relay",
        "--dir",
        "/workspace",
        "--ro-bind",
        str(benchmark_venv),
        "/benchmark-venv",
        "--bind",
        str(workspace.workspace_dir),
        "/workspace",
        "--ro-bind",
        str(workspace.public_dir),
        "/workspace/input",
        "--ro-bind",
        str(executable_dir if bind_executable_dir else executable),
        "/agent-bin" if bind_executable_dir else sandbox_executable,
        "--ro-bind",
        str(relay_forwarder),
        "/agent-bin/relay_forwarder.py",
        "--ro-bind",
        str(relay_socket),
        "/relay/agent.sock",
        "--bind",
        str(codex_home),
        "/tmp/codex-home",
    ]
    current = Path("/")
    for part in benchmark_python_home.parent.parts[1:]:
        current /= part
        argv.extend(["--dir", str(current)])
    argv.extend(
        [
            "--ro-bind",
            str(benchmark_python_link_home),
            str(benchmark_python_link_home),
            "--ro-bind",
            str(benchmark_python_home),
            str(benchmark_python_home),
        ]
    )
    if gpu_id is not None:
        uuid_result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=uuid",
                "--format=csv,noheader",
                "-i",
                str(gpu_id),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        minor_result = subprocess.run(
            ["nvidia-smi", "-q", "-i", str(gpu_id)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if uuid_result.returncode or minor_result.returncode:
            raise AdapterError(f"could not resolve GPU {gpu_id} for the MLE sandbox")
        minor = next(
            (
                line.split(":", 1)[1].strip()
                for line in minor_result.stdout.splitlines()
                if "Minor Number" in line and ":" in line
            ),
            "",
        )
        if not minor.isdigit():
            raise AdapterError(f"could not resolve GPU device minor for GPU {gpu_id}")
        for device in (
            Path(f"/dev/nvidia{minor}"),
            Path("/dev/nvidiactl"),
            Path("/dev/nvidia-uvm"),
            Path("/dev/nvidia-uvm-tools"),
        ):
            if device.exists():
                argv.extend(["--dev-bind", str(device), str(device)])
    argv.extend(
        [
            "--chdir",
            "/workspace",
            "/benchmark-venv/bin/python",
            "/agent-bin/relay_forwarder.py",
            "--socket",
            "/relay/agent.sock",
            "--port",
            "6200",
            "--",
            sandbox_executable,
        ]
    )
    argv.extend(command)
    return tuple(argv)


def public_task_dir(request: MleLiteRequest) -> Path:
    return require_directory(
        request.data_root / request.competition_id / "prepared" / "public",
        "MLE-Bench public task directory",
    )


def _copy_sample_submission(public_dir: Path, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    exact_names = (
        "sample_submission.csv",
        "sampleSubmission.csv",
        "sample_submission_null.csv",
    )
    for name in exact_names:
        candidate = public_dir / name
        if candidate.is_file():
            shutil.copy2(candidate, destination)
            return destination

    candidates = sorted(
        path
        for path in public_dir.rglob("*")
        if path.is_file()
        and ("submission" in path.name.lower() or "sample" in path.name.lower())
        and path.suffix.lower() == ".csv"
    )
    if candidates:
        shutil.copy2(candidates[0], destination)
        return destination

    archives = sorted(
        path
        for path in public_dir.rglob("*.zip")
        if "submission" in path.name.lower() or "sample" in path.name.lower()
    )
    for archive_path in archives:
        with zipfile.ZipFile(archive_path) as archive:
            members = sorted(
                name for name in archive.namelist() if name.lower().endswith(".csv")
            )
            if members:
                with archive.open(members[0]) as source, destination.open("wb") as target:
                    shutil.copyfileobj(source, target)
                return destination

    raise AdapterError(f"no public sample submission found under {public_dir}")


def prepare_workspace(request: MleLiteRequest) -> MleLiteWorkspace:
    public_dir = public_task_dir(request)
    description = require_file(public_dir / "description.md", "MLE-Bench description")
    output_dir = protect_generated_output(request.output_dir, ROOT)
    workspace_dir = output_dir / "workspace"
    if workspace_dir.exists():
        if not request.force:
            raise AdapterError(f"workspace already exists: {workspace_dir}")
        shutil.rmtree(workspace_dir)
    workspace_dir.mkdir(parents=True, exist_ok=True)

    input_path = workspace_dir / "input"
    input_path.mkdir()
    shutil.copy2(description, workspace_dir / "description.md")
    sample = _copy_sample_submission(public_dir, workspace_dir / "sample_submission.csv")
    (workspace_dir / "AGENT_TASK.md").write_text(
        task_spec_text("mle-bench-lite")
        + f"\n\nCurrent competition: `{request.competition_id}`\n",
        encoding="utf-8",
    )
    return MleLiteWorkspace(
        competition_id=request.competition_id,
        public_dir=public_dir,
        workspace_dir=workspace_dir,
        description_path=workspace_dir / "description.md",
        sample_submission_path=sample,
    )


def _docker_command(request: MleLiteRequest) -> CommandSpec:
    if not request.model:
        raise AdapterError("MLE launcher requires an explicit model")
    agent_name = NATIVE_DOCKER_NAMES[request.agent]
    run_tag = request.run_tag or f"adapter_{request.agent}_{request.competition_id}"
    return CommandSpec(
        argv=(
            "bash",
            str(ROOT / "docker-eval" / "run_in_docker.sh"),
            agent_name,
            request.competition_id,
            str(request.gpu_id),
            str(request.steps),
            str(request.timeout_seconds),
        ),
        cwd=ROOT / "docker-eval",
        env={
            **_host_relay_environment(request),
            "MODEL": request.model,
            "RUN_TAG": run_tag,
            "UPSTREAM_BASE_URL": request.upstream_base_url,
            "LLM_UPSTREAM_PROXY": request.proxy,
            "LLM_FORCE_PARAMETERS_JSON": json.dumps(request.model_parameters, sort_keys=True),
            "LLM_UPSTREAM_TIMEOUT": (
                ""
                if request.request_timeout_seconds is None
                else str(request.request_timeout_seconds)
            ),
            "LLM_MAX_RETRIES": str(
                int(
                    request.retry_policy.get(
                        "max_retries",
                        max(0, int(request.retry_policy.get("max_attempts", 1)) - 1),
                    )
                )
            ),
            "BENCHMARK_TASK_SPEC_SHA256": task_spec_digest("mle-bench-lite"),
            "ARBOR_OUTPUT_DIR": str(request.output_dir.resolve()),
            "MLE_RUN_ROOT": str(request.output_dir.resolve()),
            "MLE_BENCH_DATA_ROOT": str(request.data_root.resolve()),
            "EAR_AGENT_DIR": str(AGENTS["ear"].install_path),
            "EAR_CLI_MODE": "g3_legacy",
            "EAR_OUTPUT_DIR": str(request.output_dir.resolve()),
            "MLE_AGENT_DIR": str(AGENTS["mlevolve"].install_path),
            "SEED": str(request.seed),
        },
        timeout_seconds=request.timeout_seconds + 120,
        label=f"{request.agent} MLE-Bench Lite launcher",
        inherit_env=False,
        artifact_path=request.output_dir.resolve() / "submission.csv",
    )


def _workspace_command(
    request: MleLiteRequest,
    workspace: MleLiteWorkspace,
    *,
    preview: bool = False,
) -> CommandSpec:
    if not request.model:
        raise AdapterError("MLE launcher requires an explicit model")
    instruction = request.instruction or task_spec_text("mle-bench-lite")
    environment = relay_client_env(
        base_url="http://127.0.0.1:6200/v1",
        proxy="",
        model=request.model,
        include_credentials=False,
    )
    environment.update(
        {
            "CODEX_HOME": "/tmp/codex-home",
            "HOME": "/tmp/home",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PATH": "/benchmark-venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            "TMPDIR": "/tmp",
            "XDG_CACHE_HOME": "/tmp/xdg-cache",
            "XDG_CONFIG_HOME": "/tmp/xdg-config",
            "NO_PROXY": "localhost,127.0.0.1",
            "no_proxy": "localhost,127.0.0.1",
            "OPENAI_API_KEY": "proxy",
            "ANTHROPIC_API_KEY": "proxy",
        }
    )
    if not preview:
        gpu_uuid = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=uuid",
                "--format=csv,noheader",
                "-i",
                str(request.gpu_id),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if gpu_uuid.returncode or not gpu_uuid.stdout.strip():
            raise AdapterError(f"could not resolve GPU {request.gpu_id} for the MLE workspace")
        environment["CUDA_VISIBLE_DEVICES"] = gpu_uuid.stdout.strip()
    if request.agent == "codex":
        executable = _required_executable("codex")
        native_argv = (
            "exec",
            "--ephemeral",
            "--skip-git-repo-check",
            "--sandbox",
            "workspace-write",
            "--model",
            request.model,
            "-c",
            'model_provider="benchmark_relay"',
            "-c",
            'model_providers.benchmark_relay.name="Benchmark relay"',
            "-c",
            'model_providers.benchmark_relay.base_url="http://127.0.0.1:6200/v1"',
            "-c",
            'model_providers.benchmark_relay.wire_api="responses"',
            "-c",
            "model_providers.benchmark_relay.requires_openai_auth=true",
            instruction,
        )
    elif request.agent == "claude-code":
        executable = _required_executable("claude")
        native_argv = (
            "--print",
            "--bare",
            "--no-session-persistence",
            "--model",
            request.model,
            "--permission-mode",
            "bypassPermissions",
            "--max-turns",
            str(request.max_turns),
            instruction,
        )
    else:
        raise UnsupportedAdapterError(
            f"{request.agent} does not use the generic MLE workspace adapter"
        )
    argv = (
        (str(_required_executable("bwrap")), "<sandbox-options>", str(executable), *native_argv)
        if preview
        else _workspace_sandbox_argv(
            executable,
            workspace,
            native_argv,
            relay_socket=request.relay_socket,
            gpu_id=request.gpu_id,
        )
    )
    return CommandSpec(
        argv=argv,
        cwd=workspace.workspace_dir,
        env=environment,
        timeout_seconds=request.timeout_seconds,
        label=f"{request.agent} MLE-Bench Lite workspace agent",
        inherit_env=False,
        artifact_path=workspace.workspace_dir / "submission.csv",
    )


def _ai_scientist_command(request: MleLiteRequest) -> CommandSpec:
    require_clean_upstream_source("ai-scientist")
    if not request.model:
        raise AdapterError("MLE launcher requires an explicit model")
    public_dir = public_task_dir(request)
    profile_path = request.output_dir.resolve() / "ai-scientist-llm-profile.yaml"
    profile_name = request.official_llm_profile or "benchmark-model"
    if not request.dry_run and request.official_llm_profile is None:
        max_tokens = request.model_parameters.get(
            "max_completion_tokens",
            request.model_parameters.get(
                "max_output_tokens",
                request.model_parameters.get("max_tokens", 32768),
            ),
        )
        api_mode = (
            "completions"
            if request.model_parameters.get("use_completion_api") is True
            or request.model_parameters.get("api_mode") == "completions"
            else "responses"
        )
        profile: dict[str, object] = {
            "version": 1,
            "defaults": {"default": profile_name, "mle": profile_name},
            "backends": {
                "benchmark-relay": {
                    "type": "openai",
                    "env": {
                        "api_key": {"var": "OPENAI_API_KEY", "required": True},
                        "base_url": {"var": "OPENAI_BASE_URL", "required": True},
                    },
                }
            },
            "profiles": {
                profile_name: {
                    "backend": "benchmark-relay",
                    "model": request.model,
                    "api": api_mode,
                    "limits": {"max_completion_tokens": int(max_tokens)},
                }
            },
        }
        configured = profile["profiles"][profile_name]
        context_window = request.model_parameters.get("context_window")
        reasoning_effort = request.model_parameters.get("reasoning_effort")
        temperature = request.model_parameters.get("temperature")
        if context_window is not None:
            configured["limits"]["context_window"] = int(context_window)
        if reasoning_effort is not None:
            configured["reasoning"] = {"effort": str(reasoning_effort)}
        if temperature is not None:
            configured["sampling"] = {"temperature": float(temperature)}
        try:
            with profile_path.open("x", encoding="utf-8") as handle:
                json.dump(profile, handle, sort_keys=True, indent=2)
                handle.write("\n")
        except FileExistsError as exc:
            raise AdapterError(
                f"refusing to overwrite AiScientist profile: {profile_path}"
            ) from exc
    executable_path = (
        AGENTS[request.agent].execution_path / ".venv" / "bin" / "aisci"
    )
    if request.dry_run:
        executable = executable_path
    else:
        require_file(executable_path, "AiScientist executable")
        executable = executable_path
    native_argv = [
            str(executable),
            "--output-root",
            str(request.output_dir.resolve()),
    ]
    if request.official_llm_profile is None:
        native_argv.extend(["--llm-profile-file", str(profile_path)])
    native_argv.extend([
            "mle",
            "run",
            "--data-dir",
            str(public_dir.resolve()),
            "--llm-profile",
            profile_name,
            "--gpu-ids",
            str(request.gpu_id),
            "--time-limit",
            f"{request.timeout_seconds}s",
            "--wait",
            "--skip-final-validation",
    ])
    if request.runtime_image:
        native_argv.extend(["--image", request.runtime_image])
    if request.image_pull_policy:
        native_argv.extend(["--pull-policy", request.image_pull_policy])
    wrapper_argv = (
             str(ROOT / "BenchmarkAdapters/.venv/bin/python"),
            str(Path(__file__).with_name("native_wrappers.py")),
            "ai-scientist",
            "--output-dir",
            str(request.output_dir.resolve()),
            "--",
             *native_argv,
    )
    environment = relay_client_env(
        base_url="http://127.0.0.1:6200/v1",
        proxy="",
        model=request.model,
        include_credentials=False,
    )
    environment.update(
        {
            "HOME": str(request.output_dir.resolve() / "home"),
            "OPENAI_API_KEY": "proxy",
            "ANTHROPIC_API_KEY": "proxy",
            "PYTHONPATH": str(AGENTS[request.agent].install_path / "src"),
            "TMPDIR": str(request.output_dir.resolve() / "tmp"),
            "XDG_CACHE_HOME": str(request.output_dir.resolve() / "cache"),
            "XDG_CONFIG_HOME": str(request.output_dir.resolve() / "config"),
            "BENCHMARK_TASK_SPEC_SHA256": task_spec_digest("mle-bench-lite"),
            "DOCKER_HOST": "unix:///run/docker.sock",
        }
    )
    argv = (
        (str(_required_executable("bwrap")), "<sandbox-options>", *wrapper_argv)
        if request.dry_run
        else _native_host_sandbox_argv(
            request=request,
            wrapper_argv=wrapper_argv,
            source_root=AGENTS[request.agent].install_path,
            public_dir=public_dir,
            docker_socket=Path("/run/docker.sock"),
        )
    )
    return CommandSpec(
        argv=argv,
        cwd=AGENTS[request.agent].install_path,
        env=environment,
        timeout_seconds=request.timeout_seconds + 120,
        label="AiScientist MLE-Bench Lite adapter",
        inherit_env=False,
        artifact_path=request.output_dir.resolve() / "submission.csv",
    )


def _ml_master_command(request: MleLiteRequest) -> CommandSpec:
    require_clean_upstream_source("ml-master-2")
    if not request.model:
        raise AdapterError("MLE launcher requires an explicit model")
    if request.config_path is None:
        raise AdapterError(
            "ML-Master 2 requires a generated per-run config_path; the upstream example "
            "contains task-specific paths and model settings"
        )
    config_path = require_file(request.config_path, "ML-Master 2 config")
    description = public_task_dir(request) / "description.md"
    executable = AGENTS[request.agent].execution_path / ".venv" / "bin" / "python"
    require_file(executable, "ML-Master 2 Python executable")
    workspace_dir = request.output_dir.resolve() / "workspace"
    native_argv = (
            str(executable),
            "run.py",
            "--agent",
            "ml_master_2",
            "--config",
            str(config_path.resolve()),
            "--task",
            str(description.resolve()),
            "--run-dir",
            str(request.output_dir.resolve()),
    )
    environment = relay_client_env(
        base_url="http://127.0.0.1:6200/v1",
        proxy="",
        model=request.model,
        include_credentials=False,
    )
    environment.update(
        {
            "HOME": str(request.output_dir.resolve() / "home"),
            "OPENAI_API_KEY": "proxy",
            "ANTHROPIC_API_KEY": "proxy",
            "TMPDIR": str(request.output_dir.resolve() / "tmp"),
            "XDG_CACHE_HOME": str(request.output_dir.resolve() / "cache"),
            "XDG_CONFIG_HOME": str(request.output_dir.resolve() / "config"),
            "BENCHMARK_TASK_SPEC_SHA256": task_spec_digest("mle-bench-lite"),
        }
    )
    wrapper_argv = (
             str(ROOT / "BenchmarkAdapters/.venv/bin/python"),
            str(Path(__file__).with_name("native_wrappers.py")),
            "ml-master-2",
            "--output-dir",
            str(request.output_dir.resolve()),
            "--workspace-dir",
            str(workspace_dir),
            "--",
             *native_argv,
    )
    argv = (
        (str(_required_executable("bwrap")), "<sandbox-options>", *wrapper_argv)
        if request.dry_run
        else _native_host_sandbox_argv(
            request=request,
            wrapper_argv=wrapper_argv,
            source_root=AGENTS[request.agent].install_path,
            public_dir=public_task_dir(request),
        )
    )
    return CommandSpec(
        argv=argv,
        cwd=AGENTS[request.agent].install_path,
        env=environment,
        timeout_seconds=request.timeout_seconds + 120,
        label="ML-Master 2 MLE-Bench Lite adapter",
        inherit_env=False,
        artifact_path=request.output_dir.resolve() / "submission.csv",
    )


class MleLiteAdapter:
    """Build and run the shared MLE-Bench Lite contract for one Agent."""

    def __init__(self, agent: str):
        if agent not in AGENTS:
            raise AdapterError(f"unknown baseline agent: {agent}")
        self.agent = agent

    def build_command(self, request: MleLiteRequest) -> CommandSpec:
        if request.agent != self.agent:
            raise AdapterError("request agent does not match adapter agent")
        public_dir = public_task_dir(request)
        protect_generated_output(request.output_dir, ROOT, create=not request.dry_run)
        variant = require_thin_support(
            self.agent, "mle-bench-lite", request.agent_variant
        )
        mode = AGENTS[self.agent].mle_mode
        if variant is not None:
            if variant.key != "arbor-benchmark-patched":
                raise UnsupportedAdapterError(
                    f"{variant.key} has no MLE-Bench Lite implementation"
                )
            mode = "native-docker"
        if mode == "native-docker":
            if self.agent == "arbor" and selected_variant(
                self.agent, "mle-bench-lite", request.agent_variant
            ) is None:
                raise UnsupportedAdapterError(
                    "original Arbor MLE is unsupported without a host development evaluator; "
                    "the patched MLE runtime requires arbor-benchmark-patched"
                )
            return _docker_command(request)
        if mode == "generic-mle-workspace":
            if request.dry_run:
                workspace_dir = request.output_dir.resolve() / "workspace"
                workspace = MleLiteWorkspace(
                    competition_id=request.competition_id,
                    public_dir=public_dir,
                    workspace_dir=workspace_dir,
                    description_path=workspace_dir / "description.md",
                    sample_submission_path=workspace_dir / "sample_submission.csv",
                )
            else:
                workspace = prepare_workspace(request)
            return _workspace_command(request, workspace, preview=request.dry_run)
        if self.agent == "ai-scientist":
            return _ai_scientist_command(request)
        if self.agent == "ml-master-2":
            return _ml_master_command(request)
        raise UnsupportedAdapterError(f"no MLE-Bench Lite adapter for {self.agent}")

    def run(self, request: MleLiteRequest, *, log_path: Path | None = None) -> Path:
        roots = submission_roots(request)
        previous = _submission_snapshot(*roots)
        forbidden_hashes = _sample_hashes(request)
        variant = selected_variant(
            self.agent, "mle-bench-lite", request.agent_variant
        )
        native_docker = AGENTS[self.agent].mle_mode == "native-docker" or (
            variant is not None and variant.key == "arbor-benchmark-patched"
        )
        if not native_docker:
            output_dir = protect_generated_output(request.output_dir, ROOT)
            with tempfile.TemporaryDirectory(prefix="mle-agent-relay-") as temporary:
                socket_path = Path(temporary) / "relay.sock"
                relay = RelayProcess(
                    agent=request.agent,
                    log_path=output_dir / "relay.log",
                    token_log_path=output_dir / "token_usage.jsonl",
                    unix_socket=socket_path,
                    upstream_base_url=request.upstream_base_url,
                    upstream_proxy=request.proxy,
                    model=request.model,
                    model_parameters=request.model_parameters,
                    request_timeout_seconds=request.request_timeout_seconds,
                    retry_policy=request.retry_policy,
                )
                with relay:
                    command = self.build_command(replace(request, relay_socket=socket_path))
                    result = run_command(command, log_path=log_path)
        else:
            command = self.build_command(request)
            result = run_command(command, log_path=log_path)
        if not result.succeeded:
            raise AdapterError(
                f"{command.label} exited with code {result.return_code}; "
                f"see {log_path or 'captured output'}"
            )
        if command.artifact_path is not None:
            candidate = command.artifact_path.resolve()
            fingerprint = _file_fingerprint(candidate)
            if (
                fingerprint is not None
                and fingerprint[0] > 0
                and forbidden_hashes.isdisjoint(_file_signatures(candidate))
                and (
                    previous.get(candidate) is None
                    or previous[candidate][-1] != fingerprint[-1]
                )
            ):
                return candidate
        raise AdapterError(
            f"declared final submission is missing, stale, empty, or sample-equivalent: "
            f"{command.artifact_path}"
        )


def submission_roots(request: MleLiteRequest) -> tuple[Path, ...]:
    run_tag = request.run_tag or f"adapter_{request.agent}_{request.competition_id}"
    roots = [request.output_dir.resolve()]
    if request.agent == "ear":
        roots.insert(
            0,
            AGENTS["ear"].install_path
            / "docker_runs"
            / f"{run_tag}_{request.competition_id}",
        )
    return tuple(roots)


def find_submission(
    *output_dirs: Path,
    previous: dict[Path, tuple[int, int, int, str]] | None = None,
    forbidden_hashes: set[str] | None = None,
) -> Path:
    previous = previous or {}
    forbidden_hashes = forbidden_hashes or set()
    for output_dir in output_dirs:
        for candidate in _submission_candidates(output_dir):
            fingerprint = _file_fingerprint(candidate)
            if (
                fingerprint is not None
                and fingerprint[0] > 0
                and (
                    previous.get(candidate.resolve()) is None
                    or previous[candidate.resolve()][-1] != fingerprint[-1]
                )
                and forbidden_hashes.isdisjoint(_file_signatures(candidate))
            ):
                return candidate
    searched = ", ".join(str(path) for path in output_dirs)
    raise AdapterError(f"no regular non-empty submission.csv found under {searched}")


__all__ = [
    "MleLiteAdapter",
    "MleLiteRequest",
    "MleLiteWorkspace",
    "find_submission",
    "prepare_workspace",
    "public_task_dir",
    "submission_roots",
]
