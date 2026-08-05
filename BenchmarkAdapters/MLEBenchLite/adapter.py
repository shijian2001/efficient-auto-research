"""Shared MLE-Bench Lite adapter contract."""

from __future__ import annotations

import shutil
import textwrap
import zipfile
from dataclasses import dataclass
from pathlib import Path

from ..contracts import (
    AdapterError,
    CommandSpec,
    UnsupportedAdapterError,
    require_directory,
    require_file,
)
from ..process import DEFAULT_PROXY, DEFAULT_RELAY_BASE_URL, relay_client_env, run_command
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


@dataclass(frozen=True)
class MleLiteWorkspace:
    competition_id: str
    public_dir: Path
    workspace_dir: Path
    description_path: Path
    sample_submission_path: Path


NATIVE_DOCKER_NAMES = {
    "ear": "efficient-auto-research",
    "mlevolve": "MLEvolve",
    "arbor": "Arbor",
}


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
    workspace_dir = request.output_dir.resolve() / "workspace"
    if workspace_dir.exists():
        if not request.force:
            raise AdapterError(f"workspace already exists: {workspace_dir}")
        shutil.rmtree(workspace_dir)
    workspace_dir.mkdir(parents=True, exist_ok=True)

    input_path = workspace_dir / "input"
    input_path.symlink_to(public_dir, target_is_directory=True)
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
            "ARBOR_OUTPUT_DIR": str(request.output_dir.resolve()),
            "MLE_RUN_ROOT": str(request.output_dir.resolve()),
            "EAR_AGENT_DIR": str(AGENTS["ear"].install_path),
            "MLE_AGENT_DIR": str(AGENTS["mlevolve"].install_path),
        },
        timeout_seconds=request.timeout_seconds + 120,
        label=f"{request.agent} MLE-Bench Lite launcher",
    )


def _workspace_command(request: MleLiteRequest, workspace: MleLiteWorkspace) -> CommandSpec:
    instruction = request.instruction or (
        "Solve the MLE-Bench Lite competition in this workspace. Read the task description and "
        "public data, write and run the solution, and leave submission.csv in the current directory."
    )
    environment = relay_client_env(
        base_url=request.upstream_base_url,
        proxy=request.proxy,
        model=request.model,
    )
    if request.agent == "codex":
        argv = (
            "codex",
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
        argv = (
            "claude",
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
    return CommandSpec(
        argv=argv,
        cwd=workspace.workspace_dir,
        env=environment,
        timeout_seconds=request.timeout_seconds,
        label=f"{request.agent} MLE-Bench Lite workspace agent",
    )


def _ai_scientist_command(request: MleLiteRequest) -> CommandSpec:
    executable = require_file(
        AGENTS[request.agent].install_path / ".venv" / "bin" / "aisci",
        "AiScientist executable",
    )
    return CommandSpec(
        argv=(
            str(executable),
            "--output-root",
            str(request.output_dir.resolve()),
            "mle",
            "run",
            "--name",
            request.competition_id,
            "--mlebench-data-dir",
            str(request.data_root.resolve()),
            "--llm-profile",
            request.model,
            "--gpu-ids",
            str(request.gpu_id),
            "--time-limit",
            f"{request.timeout_seconds}s",
            "--wait",
        ),
        cwd=AGENTS[request.agent].install_path,
        env=relay_client_env(
            base_url=request.upstream_base_url,
            proxy=request.proxy,
            model=request.model,
        ),
        timeout_seconds=request.timeout_seconds + 120,
        label="AiScientist MLE-Bench Lite adapter",
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
    return CommandSpec(
        argv=(
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
        ),
        cwd=AGENTS[request.agent].install_path,
        env=relay_client_env(
            base_url=request.upstream_base_url,
            proxy=request.proxy,
            model=request.model,
        ),
        timeout_seconds=request.timeout_seconds + 120,
        label="ML-Master 2 MLE-Bench Lite adapter",
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
        mode = AGENTS[self.agent].mle_mode
        if mode == "native-docker":
            return _docker_command(request)
        if mode == "generic-mle-workspace":
            return _workspace_command(request, prepare_workspace(request))
        if self.agent == "ai-scientist":
            return _ai_scientist_command(request)
        if self.agent == "ml-master-2":
            return _ml_master_command(request)
        raise UnsupportedAdapterError(f"no MLE-Bench Lite adapter for {self.agent}")

    def run(self, request: MleLiteRequest, *, log_path: Path | None = None) -> Path:
        command = self.build_command(request)
        result = run_command(command, log_path=log_path)
        if not result.succeeded:
            raise AdapterError(
                f"{command.label} exited with code {result.return_code}; "
                f"see {log_path or 'captured output'}"
            )
        return find_submission(*submission_roots(request))


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


def find_submission(*output_dirs: Path) -> Path:
    for output_dir in output_dirs:
        candidates = [output_dir / "submission.csv", output_dir / "workspace" / "submission.csv"]
        candidates.extend(
            sorted(
                path
                for path in output_dir.rglob("*.csv")
                if "submission" in path.name.lower()
            )
        )
        for candidate in candidates:
            if candidate.is_file() and not candidate.is_symlink() and candidate.stat().st_size > 0:
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
