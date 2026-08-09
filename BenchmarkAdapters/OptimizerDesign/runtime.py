"""Frozen native Agent runtime identities for Optimizer Design."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

from ..contracts import AdapterError
from ..protocol import canonical_json, sha256_file, write_json_exclusive
from ..registry import AGENTS, ROOT


_IGNORED_PARTS = {".git", ".mypy_cache", ".pytest_cache", ".ruff_cache", "__pycache__"}


def directory_digest(root: Path) -> str:
    root = root.resolve()
    if not root.is_dir():
        raise AdapterError(f"Optimizer Design runtime tree is missing: {root}")
    entries: list[dict[str, str]] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(part in _IGNORED_PARTS for part in relative.parts) or path.suffix == ".pyc":
            continue
        if path.is_symlink():
            entries.append({"path": relative.as_posix(), "symlink": os.readlink(path)})
        elif path.is_file():
            entries.append({"path": relative.as_posix(), "sha256": sha256_file(path)})
    return hashlib.sha256(canonical_json({"entries": entries})).hexdigest()


def python_package_fingerprint(python: Path) -> str:
    completed = subprocess.run(
        [
            str(python),
            "-c",
            (
                "import hashlib,json; from importlib.metadata import distributions; "
                "items=sorted((d.metadata.get('Name','').lower(),d.version) for d in distributions()); "
                "print(hashlib.sha256(json.dumps(items,separators=(',',':')).encode()).hexdigest())"
            ),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode or len(completed.stdout.strip()) != 64:
        raise AdapterError(f"could not fingerprint Optimizer Design Python runtime: {python}")
    return completed.stdout.strip()


@dataclass(frozen=True)
class AgentRuntimeRecord:
    kind: str
    executable_path: str
    executable_sha256: str
    version: str
    project_path: str | None
    pyproject_sha256: str | None
    lock_sha256: str | None
    source_snapshot_path: str | None
    source_snapshot_sha256: str | None
    package_fingerprint: str | None
    environment_path: str | None
    environment_sha256: str | None
    distribution_path: str | None
    distribution_sha256: str | None

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(canonical_json(asdict(self))).hexdigest()

    def validate(self, agent: str) -> None:
        if self.kind not in {"python", "cli"}:
            raise AdapterError(f"invalid Optimizer Design runtime kind for {agent}")
        executable = Path(self.executable_path).expanduser().absolute()
        if not executable.is_file() or not os.access(executable, os.X_OK):
            raise AdapterError(f"Optimizer Design Agent executable is unavailable: {agent}")
        resolved_executable = executable.resolve()
        if sha256_file(resolved_executable) != self.executable_sha256:
            raise AdapterError(f"Optimizer Design Agent executable drift: {agent}")
        completed = subprocess.run(
            [str(executable), "--version"],
            capture_output=True,
            text=True,
            check=False,
        )
        observed_version = (completed.stdout or completed.stderr).strip().splitlines()
        if completed.returncode or not observed_version or observed_version[0] != self.version:
            raise AdapterError(f"Optimizer Design Agent version drift: {agent}")
        if self.kind == "cli":
            if any(
                value is not None
                for value in (
                    self.project_path,
                    self.pyproject_sha256,
                    self.lock_sha256,
                    self.source_snapshot_path,
                    self.source_snapshot_sha256,
                    self.package_fingerprint,
                    self.environment_path,
                    self.environment_sha256,
                )
            ):
                raise AdapterError(f"Optimizer Design CLI runtime schema is invalid: {agent}")
            distribution = Path(self.distribution_path or "").expanduser().resolve()
            if directory_digest(distribution) != self.distribution_sha256:
                raise AdapterError(f"Optimizer Design CLI distribution drift: {agent}")
            return
        if self.distribution_path is not None or self.distribution_sha256 is not None:
            raise AdapterError(f"Optimizer Design Python runtime schema is invalid: {agent}")
        if not self.version.startswith("Python "):
            raise AdapterError(f"Optimizer Design Python version identity is invalid: {agent}")
        project = (ROOT / str(self.project_path)).resolve()
        source = (ROOT / str(self.source_snapshot_path)).resolve()
        environment_root = Path(str(self.environment_path)).expanduser().absolute()
        if (
            sha256_file(project / "pyproject.toml") != self.pyproject_sha256
            or sha256_file(project / "uv.lock") != self.lock_sha256
            or directory_digest(source) != self.source_snapshot_sha256
            or python_package_fingerprint(executable) != self.package_fingerprint
            or directory_digest(environment_root) != self.environment_sha256
        ):
            raise AdapterError(f"Optimizer Design Python Agent runtime drift: {agent}")
        uv = shutil.which("uv")
        if uv is None:
            raise AdapterError("Optimizer Design Agent runtime validation requires uv")
        environment = os.environ.copy()
        environment.update({"UV_PROJECT_ENVIRONMENT": str(executable.parent.parent), "UV_NO_SYNC": "1"})
        locked = subprocess.run(
            [
                uv,
                "sync",
                "--project",
                str(project),
                "--python",
                str(executable),
                "--locked",
                "--offline",
                "--dry-run",
            ],
            cwd=project,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        if locked.returncode:
            raise AdapterError(f"Optimizer Design Python Agent lock validation failed: {agent}")


@dataclass(frozen=True)
class AgentRuntimeManifest:
    schema_version: int
    protocol_id: str
    agents: Mapping[str, AgentRuntimeRecord]

    @property
    def digest(self) -> str:
        return hashlib.sha256(canonical_json(self.to_dict())).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "protocol_id": self.protocol_id,
            "agents": {
                agent: asdict(record) for agent, record in sorted(self.agents.items())
            },
        }

    def validate(self, agent: str | None = None) -> None:
        if self.schema_version != 1:
            raise AdapterError("unsupported Optimizer Design Agent runtime manifest schema")
        if self.protocol_id != "modded-nanogpt-optimizer-design-reconstruction-v1":
            raise AdapterError("Optimizer Design Agent runtimes use a different protocol ID")
        if set(self.agents) != set(AGENTS):
            raise AdapterError("Optimizer Design Agent runtime manifest must cover all seven Agents")
        for name, record in self.agents.items():
            for digest in (
                record.executable_sha256,
                record.pyproject_sha256,
                record.lock_sha256,
                record.source_snapshot_sha256,
                record.package_fingerprint,
                record.environment_sha256,
                record.distribution_sha256,
            ):
                if digest is not None and (
                    len(digest) != 64
                    or any(character not in "0123456789abcdef" for character in digest)
                ):
                    raise AdapterError(f"invalid Optimizer Design Agent runtime digest: {name}")
            if not record.version.strip():
                raise AdapterError(f"Optimizer Design Agent runtime version is missing: {name}")
        if agent is not None:
            if agent not in self.agents:
                raise AdapterError(f"unknown Optimizer Design Agent runtime: {agent}")
            self.agents[agent].validate(agent)

    @classmethod
    def load(cls, path: Path) -> "AgentRuntimeManifest":
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            expected = payload.pop("manifest_digest", None)
            manifest = cls(
                schema_version=int(payload["schema_version"]),
                protocol_id=str(payload["protocol_id"]),
                agents={
                    str(agent): AgentRuntimeRecord(**record)
                    for agent, record in payload["agents"].items()
                },
            )
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise AdapterError(f"invalid Optimizer Design Agent runtime manifest: {path}") from exc
        manifest.validate()
        if expected != manifest.digest:
            raise AdapterError(f"Optimizer Design Agent runtime manifest digest mismatch: {path}")
        return manifest


def freeze_agent_runtime_manifest_candidate(
    *, source_manifest_path: Path, output_path: Path
) -> AgentRuntimeManifest:
    try:
        source = json.loads(source_manifest_path.read_text(encoding="utf-8"))
        source_agents = source["agents"]
        protocol_id = str(source["protocol_id"])
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise AdapterError(
            f"invalid Optimizer Design runtime source manifest: {source_manifest_path}"
        ) from exc
    if set(source_agents) != set(AGENTS):
        raise AdapterError("Optimizer Design runtime source must cover all seven Agents")
    records: dict[str, AgentRuntimeRecord] = {}
    for agent, source_record in source_agents.items():
        kind = str(source_record.get("kind", ""))
        executable = Path(str(source_record.get("executable_path", ""))).expanduser().absolute()
        if not executable.is_file() or not os.access(executable, os.X_OK):
            raise AdapterError(f"Optimizer Design Agent executable is unavailable: {agent}")
        version_result = subprocess.run(
            [str(executable), "--version"],
            capture_output=True,
            text=True,
            check=False,
        )
        version_lines = (version_result.stdout or version_result.stderr).strip().splitlines()
        if version_result.returncode or not version_lines:
            raise AdapterError(f"Optimizer Design Agent version probe failed: {agent}")
        if kind == "cli":
            distribution = Path(
                str(source_record.get("distribution_path", ""))
            ).expanduser().resolve()
            record = AgentRuntimeRecord(
                kind=kind,
                executable_path=str(executable),
                executable_sha256=sha256_file(executable.resolve()),
                version=version_lines[0],
                project_path=None,
                pyproject_sha256=None,
                lock_sha256=None,
                source_snapshot_path=None,
                source_snapshot_sha256=None,
                package_fingerprint=None,
                environment_path=None,
                environment_sha256=None,
                distribution_path=str(distribution),
                distribution_sha256=directory_digest(distribution),
            )
        elif kind == "python":
            project_relative = str(source_record.get("project_path", ""))
            snapshot_relative = str(source_record.get("source_snapshot_path", ""))
            project = (ROOT / project_relative).resolve()
            snapshot = (ROOT / snapshot_relative).resolve()
            environment = Path(
                str(source_record.get("environment_path", ""))
            ).expanduser().absolute()
            record = AgentRuntimeRecord(
                kind=kind,
                executable_path=str(executable),
                executable_sha256=sha256_file(executable.resolve()),
                version=version_lines[0],
                project_path=project_relative,
                pyproject_sha256=sha256_file(project / "pyproject.toml"),
                lock_sha256=sha256_file(project / "uv.lock"),
                source_snapshot_path=snapshot_relative,
                source_snapshot_sha256=directory_digest(snapshot),
                package_fingerprint=python_package_fingerprint(executable),
                environment_path=str(environment),
                environment_sha256=directory_digest(environment),
                distribution_path=None,
                distribution_sha256=None,
            )
        else:
            raise AdapterError(f"unsupported Optimizer Design runtime kind: {agent}")
        records[str(agent)] = record
    manifest = AgentRuntimeManifest(
        schema_version=1,
        protocol_id=protocol_id,
        agents=records,
    )
    manifest.validate()
    write_json_exclusive(
        output_path,
        {**manifest.to_dict(), "manifest_digest": manifest.digest},
    )
    return manifest


__all__ = [
    "AgentRuntimeManifest",
    "AgentRuntimeRecord",
    "directory_digest",
    "freeze_agent_runtime_manifest_candidate",
    "python_package_fingerprint",
]
