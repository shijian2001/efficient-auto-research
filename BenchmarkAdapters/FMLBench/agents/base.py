"""Uniform thin-adapter interface for all formal FML Agents."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import subprocess
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from ...contracts import AdapterError, CommandResult, CommandSpec
from ...formal_contract import ModelTrackConfig, assert_no_secrets
from ...process import redact_sensitive_payload, relay_client_env, run_command
from ...protocol import canonical_json, sha256_file, write_json_exclusive
from ...registry import AGENTS, ROOT
from ...security import is_sensitive_name
from ..task import FMLTaskSpec
from ..workspace import FMLWorkspace


@dataclass(frozen=True)
class FMLInstallationReport:
    agent_id: str
    ready: bool
    native_entrypoint: str
    executable_path: str | None
    version: str | None
    executable_sha256: str | None
    failure_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FMLAgentIdentity:
    agent_id: str
    variant: str
    source_commit: str
    source_dirty: bool
    native_entrypoint: str
    executable_path: str
    executable_version: str | None
    executable_sha256: str

    @property
    def digest(self) -> str:
        return hashlib.sha256(canonical_json(asdict(self))).hexdigest()


@dataclass(frozen=True)
class FMLAgentLaunchContext:
    agent_id: str
    agent_variant: str
    task: FMLTaskSpec
    workspace: FMLWorkspace
    output_dir: Path
    development_socket: Path
    development_token: str
    development_client_path: Path
    model_config: ModelTrackConfig
    outer_run_id: int
    timeout_seconds: int
    credential_env_names: tuple[str, ...]
    runtime_executable: Path | None = None
    formal: bool = True

    @property
    def trajectory_path(self) -> Path:
        return self.output_dir / "native-result.json"

    @property
    def development_command(self) -> str:
        return shlex.join(
            (
                str(Path("/usr/bin/python3")),
                str(self.development_client_path),
                f"--socket={self.development_socket}",
                f"--token={self.development_token}",
            )
        )

    @property
    def auditable_development_command(self) -> str:
        return self.development_command.replace(
            self.development_token, "<redacted-capability-token>"
        )


@dataclass(frozen=True)
class FMLAgentRunResult:
    schema_version: int
    agent_id: str
    agent_identity: Mapping[str, Any]
    task_id: str
    run_id: str
    outer_run_index: int
    status: str
    started_at: str
    finished_at: str
    wall_clock_seconds: float
    artifact_paths: tuple[str, ...]
    artifact_sha256: str | None
    trajectory_path: str | None
    trajectory_sha256: str | None
    stdout_path: str
    stderr_path: str
    exit_code: int | None
    failure_kind: str | None
    model_track_digest: str
    rendered_prompt_digest: str
    canonical_task_digest: str
    development_evaluator_calls: int
    generated_config_digests: Mapping[str, str] = field(default_factory=dict)
    token_usage: Mapping[str, int] = field(default_factory=dict)
    request_count: int | None = None
    cost: float | None = None

    def validate(self) -> None:
        if self.schema_version != 1 or not self.agent_id or not self.task_id or not self.run_id:
            raise AdapterError("invalid FML Agent run identity")
        if self.status not in {"completed", "failed", "timed_out", "infrastructure_error"}:
            raise AdapterError("invalid FML Agent run status")
        if self.status != "completed" and not self.failure_kind:
            raise AdapterError("failed FML Agent result requires a failure kind")
        if self.wall_clock_seconds < 0 or self.development_evaluator_calls < 0:
            raise AdapterError("invalid FML Agent runtime evidence")
        for digest in (
            self.model_track_digest,
            self.rendered_prompt_digest,
            self.canonical_task_digest,
        ):
            if len(digest) != 64:
                raise AdapterError("invalid FML Agent run digest")
        if self.artifact_sha256 is not None and len(self.artifact_sha256) != 64:
            raise AdapterError("invalid FML Agent artifact digest")
        if self.trajectory_sha256 is not None and len(self.trajectory_sha256) != 64:
            raise AdapterError("invalid FML Agent trajectory digest")
        assert_no_secrets(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["agent_identity"] = dict(sorted(self.agent_identity.items()))
        payload["artifact_paths"] = list(self.artifact_paths)
        payload["generated_config_digests"] = dict(
            sorted(self.generated_config_digests.items())
        )
        payload["token_usage"] = dict(sorted(self.token_usage.items()))
        return payload

    def write(self, path: Path) -> None:
        self.validate()
        write_json_exclusive(path, self.to_dict())


def _git_identity(path: Path) -> tuple[str, bool]:
    commit = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    dirty = subprocess.run(
        ["git", "-C", str(path), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=False,
    )
    if commit.returncode or dirty.returncode:
        raise AdapterError(f"cannot resolve FML Agent source identity: {path}")
    return commit.stdout.strip(), bool(dirty.stdout.strip())


def _version(executable: Path, arguments: tuple[str, ...]) -> str | None:
    completed = subprocess.run(
        [str(executable), *arguments], capture_output=True, text=True, timeout=30, check=False
    )
    text = (completed.stdout or completed.stderr).strip()
    return text.splitlines()[0][:500] if text else None


class FMLAgentAdapter(ABC):
    agent_id: str
    native_entrypoint: str
    version_arguments: tuple[str, ...] = ("--version",)
    installation_probe_arguments: tuple[str, ...] | None = None

    @abstractmethod
    def installation_executable(self) -> Path | None:
        """Return the exact production executable, without fallback."""

    @abstractmethod
    def build_native_command(self, context: FMLAgentLaunchContext, prompt: str) -> CommandSpec:
        """Translate the shared contract into the Agent's native invocation."""

    def validate_installation(self) -> FMLInstallationReport:
        executable = self.installation_executable()
        if executable is None or not executable.is_file() or not os.access(executable, os.X_OK):
            return FMLInstallationReport(
                agent_id=self.agent_id,
                ready=False,
                native_entrypoint=self.native_entrypoint,
                executable_path=None if executable is None else str(executable),
                version=None,
                executable_sha256=None,
                failure_reason=f"native executable is unavailable: {executable}",
            )
        version = _version(executable, self.version_arguments)
        if self.installation_probe_arguments is not None:
            completed = subprocess.run(
                [str(executable), *self.installation_probe_arguments],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            if completed.returncode:
                detail = (completed.stderr or completed.stdout).strip().splitlines()
                return FMLInstallationReport(
                    agent_id=self.agent_id,
                    ready=False,
                    native_entrypoint=self.native_entrypoint,
                    executable_path=str(executable.resolve()),
                    version=version,
                    executable_sha256=sha256_file(executable.resolve()),
                    failure_reason=(
                        "native API import probe failed: "
                        + (detail[-1][:500] if detail else f"exit {completed.returncode}")
                    ),
                )
        return FMLInstallationReport(
            agent_id=self.agent_id,
            ready=True,
            native_entrypoint=self.native_entrypoint,
            executable_path=str(executable.resolve()),
            version=version,
            executable_sha256=sha256_file(executable.resolve()),
        )

    def identity(self, context: FMLAgentLaunchContext) -> FMLAgentIdentity:
        report = self.validate_installation()
        if context.runtime_executable is None and not report.ready:
            raise AdapterError(report.failure_reason or f"FML {self.agent_id} is not installed")
        executable = context.runtime_executable or self.installation_executable()
        if executable is None or not executable.is_file():
            raise AdapterError(report.failure_reason or f"FML {self.agent_id} is not installed")
        source_commit, source_dirty = _git_identity(AGENTS[self.agent_id].install_path)
        variant = context.agent_variant.strip()
        if not variant or variant.lower() == "default":
            raise AdapterError("FML Agent variant must be explicit")
        name, separator, revision = variant.partition("@")
        if not separator or len(revision) != 40 or any(
            character not in "0123456789abcdef" for character in revision.lower()
        ):
            raise AdapterError("FML Agent variant must end in @<40-hex-commit>")
        if context.formal and revision.lower() != source_commit.lower():
            raise AdapterError("FML Agent variant commit differs from the source identity")
        if self.agent_id == "ear" and name not in {"g3", "full"}:
            raise AdapterError("EAR FML variant must be g3 or full with immutable identity")
        return FMLAgentIdentity(
            agent_id=self.agent_id,
            variant=variant,
            source_commit=source_commit,
            source_dirty=source_dirty,
            native_entrypoint=self.native_entrypoint,
            executable_path=str(executable.resolve()),
            executable_version=(
                _version(executable, self.version_arguments)
                if context.runtime_executable is None
                else "synthetic-runtime-override"
            ),
            executable_sha256=sha256_file(executable.resolve()),
        )

    def render_task_input(self, context: FMLAgentLaunchContext) -> str:
        return context.task.render(development_command=context.development_command)

    def render_auditable_task_input(self, context: FMLAgentLaunchContext) -> str:
        return context.task.render(
            development_command=context.auditable_development_command
        )

    def build_environment(self, context: FMLAgentLaunchContext) -> dict[str, str]:
        context.model_config.validate(formal=context.formal)
        if context.formal and context.model_config.request_timeout_seconds is None:
            raise AdapterError("formal FML model track requires an explicit request timeout")
        if context.formal and not context.model_config.retry_policy:
            raise AdapterError("formal FML model track requires an explicit retry policy")
        environment = relay_client_env(
            base_url=context.model_config.relay_base_url,
            model=context.model_config.outer_model_id,
            include_credentials=False,
        )
        environment.update(
            {
                "HOME": str((context.output_dir / "home").resolve()),
                "CODEX_HOME": str((context.output_dir / "codex-home").resolve()),
                "XDG_CACHE_HOME": str((context.output_dir / "xdg-cache").resolve()),
                "XDG_CONFIG_HOME": str((context.output_dir / "xdg-config").resolve()),
                "PATH": os.environ.get("PATH", ""),
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "FML_MODEL_TRACK_ID": context.model_config.model_track_id,
                "FML_MODEL_ID": context.model_config.outer_model_id,
                "FML_MODEL_PARAMETERS": json.dumps(
                    context.model_config.model_parameters, sort_keys=True
                ),
                "FML_MODEL_CONFIG_DIGEST": context.model_config.digest,
                "FML_REQUEST_TIMEOUT_SECONDS": (
                    ""
                    if context.model_config.request_timeout_seconds is None
                    else str(context.model_config.request_timeout_seconds)
                ),
                "FML_RETRY_POLICY": json.dumps(
                    context.model_config.retry_policy, sort_keys=True
                ),
                "FML_AGENT_ID": self.agent_id,
                "FML_AGENT_VARIANT": context.agent_variant,
                "FML_TASK_ID": context.task.task_id,
                "FML_TASK_SPEC_DIGEST": context.task.digest,
                "FML_CANONICAL_TASK_JSON": json.dumps(
                    context.task.agent_payload(), sort_keys=True
                ),
                "FML_WALL_CLOCK_SECONDS": str(context.timeout_seconds),
                "FML_DEVELOPMENT_TOKEN": context.development_token,
                "FML_NATIVE_RESULT_PATH": str(context.trajectory_path),
                "PYTHONHASHSEED": str(context.outer_run_id),
            }
        )
        for name in ("HOME", "CODEX_HOME", "XDG_CACHE_HOME", "XDG_CONFIG_HOME"):
            Path(environment[name]).mkdir(parents=True, exist_ok=True)
        for name in context.credential_env_names:
            if not is_sensitive_name(name):
                raise AdapterError(
                    f"FML credential environment name is not recognized as sensitive: {name}"
                )
            value = os.environ.get(name)
            if not value:
                raise AdapterError(f"required FML credential environment variable is unset: {name}")
            environment[name] = value
        credential = next(
            (os.environ.get(name) for name in context.credential_env_names if os.environ.get(name)),
            None,
        )
        if credential:
            environment.update(
                {
                    "OPENAI_API_KEY": credential,
                    "UPSTREAM_API_KEY": credential,
                    "ANTHROPIC_API_KEY": credential,
                }
            )
        elif context.formal and context.runtime_executable is None:
            raise AdapterError("formal FML launch requires an explicit relay credential")
        return environment

    def build_launch_command(self, context: FMLAgentLaunchContext) -> tuple[CommandSpec, str]:
        prompt = self.render_task_input(context)
        prompt_digest = hashlib.sha256(
            self.render_auditable_task_input(context).encode("utf-8")
        ).hexdigest()
        if context.runtime_executable is not None:
            command = CommandSpec(
                argv=(
                    str(context.runtime_executable.resolve()),
                    "--workspace",
                    str(context.workspace.root),
                    "--output-dir",
                    str(context.output_dir),
                    "--task-input",
                    prompt,
                    "--dev-command",
                    context.development_command,
                    "--agent-id",
                    self.agent_id,
                ),
                cwd=context.workspace.root,
                env=self.build_environment(context),
                timeout_seconds=context.timeout_seconds,
                label=f"FML synthetic override / {self.agent_id}",
                inherit_env=False,
            )
        else:
            command = self.build_native_command(context, prompt)
            command = CommandSpec(
                argv=command.argv,
                cwd=command.cwd,
                env={**self.build_environment(context), **dict(command.env)},
                timeout_seconds=context.timeout_seconds,
                label=command.label,
                inherit_env=False,
            )
        return command, prompt_digest

    def launch(self, context: FMLAgentLaunchContext, *, log_path: Path) -> CommandResult:
        command, _ = self.build_launch_command(context)
        result = run_command(command, log_path=log_path)
        if not context.trajectory_path.exists():
            write_json_exclusive(
                context.trajectory_path,
                {
                    "native_entrypoint": self.native_entrypoint,
                    "return_code": result.return_code,
                    "output": result.stdout,
                },
            )
        return result

    def locate_artifact(self, context: FMLAgentLaunchContext) -> tuple[Path, ...]:
        paths = tuple((context.workspace.root / value).resolve() for value in context.task.editable_paths)
        for path in paths:
            if not path.is_file() or path.is_symlink():
                raise AdapterError(f"FML {self.agent_id} omitted editable artifact: {path}")
        return paths

    def collect_trajectory(self, context: FMLAgentLaunchContext) -> tuple[Path | None, dict[str, Any]]:
        path = context.trajectory_path
        if not path.is_file() or path.is_symlink():
            return None, {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise AdapterError(f"invalid FML {self.agent_id} trajectory") from exc
        sanitized = redact_sensitive_payload(payload, self.build_environment(context))
        if sanitized != payload:
            path.unlink()
            write_json_exclusive(path, sanitized)
            payload = sanitized
        return path, payload

    def generated_config_digests(
        self, context: FMLAgentLaunchContext
    ) -> dict[str, str]:
        return {}

    def normalize_exit_status(self, result: CommandResult) -> tuple[str, str | None]:
        if result.return_code == 0:
            return "completed", None
        if result.return_code == 124:
            return "timed_out", "agent_timeout"
        return "failed", f"agent_exit_{result.return_code}"


def native_python_command(
    *,
    context: FMLAgentLaunchContext,
    prompt: str,
    python: Path,
    module: str,
    source_root: Path,
    label: str,
) -> CommandSpec:
    if not python.is_file():
        raise AdapterError(f"{label} Python is unavailable: {python}")
    return CommandSpec(
        argv=(
            str(python),
            "-m",
            module,
            "--native-run",
            "--workspace",
            str(context.workspace.root),
            "--output-dir",
            str(context.output_dir),
            "--task-input",
            prompt,
            "--dev-command",
            context.development_command,
            "--model",
            context.model_config.outer_model_id,
            "--seed",
            str(context.outer_run_id),
            "--timeout",
            str(context.timeout_seconds),
            "--max-steps",
            str(context.task.max_agent_steps),
        ),
        cwd=context.workspace.root,
        env={"PYTHONPATH": f"{ROOT}:{source_root.resolve()}"},
        timeout_seconds=context.timeout_seconds,
        label=label,
        inherit_env=False,
    )


__all__ = [
    "FMLAgentAdapter",
    "FMLAgentIdentity",
    "FMLAgentLaunchContext",
    "FMLAgentRunResult",
    "FMLInstallationReport",
    "native_python_command",
]
