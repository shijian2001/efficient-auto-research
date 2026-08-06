"""Workspace and command isolation for repository optimization."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..contracts import AdapterError


@dataclass(frozen=True)
class SandboxResult:
    return_code: int
    output: str


class RepositorySandbox:
    def __init__(self, workspace: Path, *, command_timeout_seconds: int):
        self.workspace = workspace.resolve()
        self.command_timeout_seconds = command_timeout_seconds
        if shutil.which("bwrap") is None:
            raise AdapterError("bubblewrap is required for repository command isolation")

    def resolve(self, relative_path: str) -> Path:
        candidate = (self.workspace / relative_path).resolve(strict=False)
        try:
            candidate.relative_to(self.workspace)
        except ValueError as exc:
            raise AdapterError(f"path escapes repository workspace: {relative_path}") from exc
        return candidate

    def read_text(self, relative_path: str, *, offset: int = 1, limit: int = 400) -> str:
        if offset < 1 or limit < 1 or limit > 2000:
            raise AdapterError("read_file offset must be >=1 and limit must be between 1 and 2000")
        path = self.resolve(relative_path)
        if not path.is_file():
            raise AdapterError(f"file does not exist: {relative_path}")
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        selected = lines[offset - 1 : offset - 1 + limit]
        return "\n".join(f"{number}: {line}" for number, line in enumerate(selected, offset))

    def write_text(self, relative_path: str, content: str) -> str:
        path = self.resolve(relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return f"wrote {len(content.encode('utf-8'))} bytes to {relative_path}"

    def replace_text(self, relative_path: str, old: str, new: str) -> str:
        if not old:
            raise AdapterError("replace_in_file old text cannot be empty")
        path = self.resolve(relative_path)
        if not path.is_file():
            raise AdapterError(f"file does not exist: {relative_path}")
        content = path.read_text(encoding="utf-8")
        matches = content.count(old)
        if matches != 1:
            raise AdapterError(
                f"replace_in_file requires exactly one match in {relative_path}; found {matches}"
            )
        path.write_text(content.replace(old, new, 1), encoding="utf-8")
        return f"replaced one occurrence in {relative_path}"

    def list_files(self, pattern: str = "*") -> str:
        pattern_path = Path(pattern)
        if pattern_path.is_absolute() or ".." in pattern_path.parts:
            raise AdapterError("list_files pattern must stay inside the repository")
        paths = sorted(
            path.relative_to(self.workspace).as_posix()
            for path in self.workspace.glob(pattern)
            if ".git" not in path.parts
        )
        return "\n".join(paths[:2000])

    def run_shell(self, command: str, *, timeout_seconds: int | None = None) -> SandboxResult:
        timeout = timeout_seconds or self.command_timeout_seconds
        timeout = max(1, min(timeout, self.command_timeout_seconds))
        environment = self._environment()
        argv = self._base_command() + ["/bin/bash", "-lc", command]
        try:
            completed = subprocess.run(
                argv,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=timeout,
            )
            output = completed.stdout or ""
            return SandboxResult(completed.returncode, output[-30000:])
        except subprocess.TimeoutExpired as exc:
            output = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
            return SandboxResult(124, (output + f"\ncommand timed out after {timeout}s")[-30000:])

    def run_evaluator(
        self,
        *,
        python_executable: str,
        evaluator: Path,
        dev_data: Path,
        concurrency: int,
    ) -> SandboxResult:
        evaluator = evaluator.resolve()
        dev_data = dev_data.resolve()
        environment = self._environment()
        environment.update(
            {
                "HARBOR_N_CONCURRENT": str(concurrency),
                "PYTHONPATH": "/workspace:/evaluator",
            }
        )
        argv = self._base_command()
        argv.extend(["--dir", "/evaluator", "--ro-bind", str(evaluator), "/evaluator/run_eval.py"])
        argv.extend(["--dir", "/benchmark", "--ro-bind", str(dev_data), "/benchmark/dev-data"])
        resolved_python = shutil.which(python_executable) if "/" not in python_executable else python_executable
        if not resolved_python:
            raise AdapterError(f"evaluator Python executable not found: {python_executable}")
        executable_path = Path(resolved_python)
        if not executable_path.is_absolute():
            executable_path = executable_path.absolute()
        resolved_python_path = executable_path.resolve()
        runtime_root = self._python_runtime_root(executable_path)
        if runtime_root is not None:
            argv.extend(self._absolute_read_bind(runtime_root))
            if executable_path.is_symlink():
                link_target = Path(os.readlink(executable_path))
                if link_target.is_absolute():
                    alias_root = link_target.parent.parent
                    if alias_root != runtime_root:
                        argv.extend(self._absolute_read_bind(alias_root))
            interpreter_root = resolved_python_path.parent.parent
            if interpreter_root != runtime_root:
                argv.extend(self._absolute_read_bind(interpreter_root))
            environment["VIRTUAL_ENV"] = str(runtime_root)
            environment["PATH"] = f"{runtime_root}/bin:{interpreter_root}/bin:{environment['PATH']}"
            sandbox_python = str(executable_path)
        else:
            interpreter_root = resolved_python_path.parent.parent
            if not self._covered_by_system_mount(interpreter_root):
                argv.extend(self._absolute_read_bind(interpreter_root))
            sandbox_python = str(resolved_python_path)
        argv.extend(
            [
                sandbox_python,
                "/evaluator/run_eval.py",
                "--data",
                "/benchmark/dev-data",
            ]
        )
        try:
            completed = subprocess.run(
                argv,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=self.command_timeout_seconds,
            )
            return SandboxResult(completed.returncode, (completed.stdout or "")[-30000:])
        except subprocess.TimeoutExpired as exc:
            output = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
            return SandboxResult(
                124,
                (output + f"\ndevelopment evaluator timed out after {self.command_timeout_seconds}s")[-30000:],
            )

    def _base_command(self) -> list[str]:
        command = [
            "bwrap",
            "--die-with-parent",
            "--unshare-pid",
            "--unshare-net",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
        ]
        for path in ("/usr", "/bin", "/sbin", "/lib", "/lib64"):
            if Path(path).exists():
                command.extend(["--ro-bind", path, path])
        command.extend(["--tmpfs", "/tmp", "--dir", "/workspace"])
        command.extend(["--bind", str(self.workspace), "/workspace"])
        command.extend(["--chdir", "/workspace"])
        return command

    def _environment(self) -> dict[str, str]:
        return {
            "HOME": "/workspace",
            "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
            "TMPDIR": "/tmp",
            "PYTHONDONTWRITEBYTECODE": "1",
            "NO_PROXY": "*",
            "no_proxy": "*",
            "LANG": os.environ.get("LANG", "C.UTF-8"),
        }

    def _absolute_read_bind(self, source: Path) -> list[str]:
        command: list[str] = []
        current = Path("/")
        for part in source.parts[1:-1]:
            current /= part
            command.extend(["--dir", str(current)])
        command.extend(["--ro-bind", str(source), str(source)])
        return command

    def _covered_by_system_mount(self, path: Path) -> bool:
        return any(
            path == root or root in path.parents
            for root in (Path("/usr"), Path("/bin"), Path("/sbin"), Path("/lib"), Path("/lib64"))
        )

    def _python_runtime_root(self, executable: Path) -> Path | None:
        candidate = executable.resolve(strict=False)
        if executable.is_absolute() and executable.parent.name == "bin":
            runtime_root = executable.parent.parent.resolve()
            if (runtime_root / "pyvenv.cfg").is_file():
                return runtime_root
        if candidate.parent.name == "bin":
            runtime_root = candidate.parent.parent
            if (runtime_root / "pyvenv.cfg").is_file():
                return runtime_root
        return None


__all__ = ["RepositorySandbox", "SandboxResult"]
