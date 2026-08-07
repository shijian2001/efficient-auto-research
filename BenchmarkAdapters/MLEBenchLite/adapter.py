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
from dataclasses import dataclass
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
from ..process import DEFAULT_PROXY, DEFAULT_RELAY_BASE_URL, relay_client_env, run_command
from ..relay import RelayProcess
from ..registry import AGENTS, ROOT


@dataclass(frozen=True)
class MleLiteRequest:
    agent: str
    competition_id: str
    data_root: Path
    output_dir: Path
    gpu_id: int = 0
    steps: int = 1
    timeout_seconds: int = 900
    model: str = "gpt-5.5"
    upstream_base_url: str = DEFAULT_RELAY_BASE_URL
    proxy: str = DEFAULT_PROXY
    run_tag: str | None = None
    instruction: str | None = None
    max_turns: int = 8
    config_path: Path | None = None
    force: bool = False
    dry_run: bool = False
    relay_socket: Path | None = None
    seed: int = 0


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
        ROOT / "BenchmarkAdapters/unix_relay_forwarder.py",
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
    sandbox_executable = f"/agent-bin/{executable.name}"
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
        str(executable),
        sandbox_executable,
        "--ro-bind",
        str(relay_forwarder),
        "/agent-bin/relay_forwarder.py",
        "--ro-bind",
        str(relay_socket),
        "/relay/agent.sock",
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
        textwrap.dedent(
            f"""
            # MLE-Bench Lite task

            Competition: `{request.competition_id}`

            Read `description.md` and the public files below `input/`. Do not search for
            private labels or any path outside this workspace. Build a reproducible public-data
            solution and leave a regular file named `submission.csv` in this directory.
            `sample_submission.csv` is format guidance only; it is not a score.
            """
        ).strip()
        + "\n",
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
    agent_name = NATIVE_DOCKER_NAMES[request.agent]
    run_tag = request.run_tag or f"adapter_{request.agent}_{request.competition_id}"
    return CommandSpec(
        argv=(
            str(ROOT / "docker-eval" / "run_in_docker.sh"),
            agent_name,
            request.competition_id,
            str(request.gpu_id),
            str(request.steps),
            str(request.timeout_seconds),
        ),
        cwd=ROOT / "docker-eval",
        env={
            "MODEL": request.model,
            "RUN_TAG": run_tag,
            "UPSTREAM_BASE_URL": request.upstream_base_url,
            "LLM_UPSTREAM_PROXY": request.proxy,
            "LLM_REASONING_EFFORT": "high",
            "LLM_TEMPERATURE": "1.0",
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
        artifact_path=request.output_dir.resolve() / "submission.csv",
    )


def _workspace_command(
    request: MleLiteRequest,
    workspace: MleLiteWorkspace,
    *,
    preview: bool = False,
) -> CommandSpec:
    instruction = request.instruction or (
        "Solve the MLE-Bench Lite competition in this workspace. Read the task description and "
        "public data, write and run the solution, and leave submission.csv in the current directory."
    )
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
    public_dir = public_task_dir(request)
    executable = require_file(
        AGENTS[request.agent].install_path / ".venv" / "bin" / "aisci",
        "AiScientist executable",
    )
    native_argv = (
            str(executable),
            "--output-root",
            str(request.output_dir.resolve()),
            "mle",
            "run",
            "--data-dir",
            str(public_dir.resolve()),
            "--llm-profile",
            request.model,
            "--gpu-ids",
            str(request.gpu_id),
            "--time-limit",
            f"{request.timeout_seconds}s",
            "--wait",
            "--skip-final-validation",
    )
    return CommandSpec(
        argv=(
            str(ROOT / "BenchmarkAdapters/.venv/bin/python"),
            str(Path(__file__).with_name("native_wrappers.py")),
            "ai-scientist",
            "--output-dir",
            str(request.output_dir.resolve()),
            "--",
            *native_argv,
        ),
        cwd=AGENTS[request.agent].install_path,
        env=relay_client_env(
            base_url=request.upstream_base_url,
            proxy=request.proxy,
            model=request.model,
        ),
        timeout_seconds=request.timeout_seconds + 120,
        label="AiScientist MLE-Bench Lite adapter",
        artifact_path=request.output_dir.resolve() / "submission.csv",
    )


def _ml_master_command(request: MleLiteRequest) -> CommandSpec:
    if request.config_path is None:
        raise AdapterError(
            "ML-Master 2 requires a generated per-run config_path; the upstream example "
            "contains task-specific paths and model settings"
        )
    config_path = require_file(request.config_path, "ML-Master 2 config")
    description = public_task_dir(request) / "description.md"
    executable = require_file(
        AGENTS[request.agent].install_path / ".venv" / "bin" / "python",
        "ML-Master 2 Python executable",
    )
    workspace_dir = request.output_dir.resolve() / "ml-master-workspace"
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
        base_url=request.upstream_base_url,
        proxy=request.proxy,
        model=request.model,
    )
    environment["ML_MASTER_RUN_TIMEOUT_SECONDS"] = str(request.timeout_seconds)
    return CommandSpec(
        argv=(
            str(ROOT / "BenchmarkAdapters/.venv/bin/python"),
            str(Path(__file__).with_name("native_wrappers.py")),
            "ml-master-2",
            "--output-dir",
            str(request.output_dir.resolve()),
            "--workspace-dir",
            str(workspace_dir),
            "--",
            *native_argv,
        ),
        cwd=AGENTS[request.agent].install_path,
        env=environment,
        timeout_seconds=request.timeout_seconds + 120,
        label="ML-Master 2 MLE-Bench Lite adapter",
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
        mode = AGENTS[self.agent].mle_mode
        if mode == "native-docker":
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
        if AGENTS[self.agent].mle_mode == "generic-mle-workspace":
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
