"""Install one benchmark/agent environment through UV.

The installer intentionally reuses each upstream project's own ``pyproject``
and lockfile. It only adds a generated UV requirements lock for MLEvolve,
which currently ships pinned requirements files instead of a pyproject.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = Path(__file__).with_name("manifest.toml")
DEFAULT_PROXY = "http://127.0.0.1:17892"


class InstallError(RuntimeError):
    pass


def load_manifest() -> dict:
    with MANIFEST_PATH.open("rb") as handle:
        return tomllib.load(handle)


def uv_command() -> str:
    executable = shutil.which("uv")
    if executable is None:
        raise InstallError("uv is not on PATH")
    return executable


def command_text(command: list[str]) -> str:
    return " ".join(_shell_quote(part) for part in command)


def _shell_quote(value: str) -> str:
    if value and all(character.isalnum() or character in "-._/:=" for character in value):
        return value
    return "'" + value.replace("'", "'\\''") + "'"


def environment() -> dict[str, str]:
    current = os.environ.copy()
    proxy = current.get("BENCHMARK_ADAPTERS_PROXY", DEFAULT_PROXY)
    current.setdefault("HTTP_PROXY", proxy)
    current.setdefault("HTTPS_PROXY", proxy)
    current.setdefault("http_proxy", current["HTTP_PROXY"])
    current.setdefault("https_proxy", current["HTTPS_PROXY"])
    return current


def run(command: list[str], *, dry_run: bool, cwd: Path | None = None) -> None:
    print(command_text(command))
    if dry_run:
        return
    try:
        subprocess.run(command, cwd=cwd, env=environment(), check=True)
    except subprocess.CalledProcessError as exc:
        raise InstallError(f"command failed with exit code {exc.returncode}") from exc


def project_path(relative: str) -> Path:
    path = (ROOT / relative).resolve()
    if not path.is_dir():
        raise InstallError(f"project directory does not exist: {path}")
    return path


def install_uv_project(spec: dict, *, dry_run: bool) -> None:
    project = project_path(spec["project"])
    lock_path = project / "uv.lock"
    uv = uv_command()
    if lock_path.is_file():
        run(
            [
                uv,
                "sync",
                "--project",
                str(project),
                "--python",
                str(spec["python"]),
                "--frozen",
            ],
            dry_run=dry_run,
        )
        return
    run(
        [uv, "lock", "--project", str(project), "--python", str(spec["python"])],
        dry_run=dry_run,
    )
    run(
        [
            uv,
            "sync",
            "--project",
            str(project),
            "--python",
            str(spec["python"]),
            "--frozen",
        ],
        dry_run=dry_run,
    )


def venv_python(venv: Path) -> Path:
    return venv / "bin" / "python"


def install_requirements(spec: dict, *, dry_run: bool) -> None:
    uv = uv_command()
    requirements_in = (ROOT / spec["requirements_in"]).resolve()
    lock_path = (ROOT / spec["lock"]).resolve()
    venv = (ROOT / spec["venv"]).resolve()
    if not requirements_in.is_file():
        raise InstallError(f"requirements input does not exist: {requirements_in}")

    compile_command = [
        uv,
        "pip",
        "compile",
        str(requirements_in),
        "--python-version",
        str(spec["python"]),
        "--output-file",
        str(lock_path),
    ]
    if not lock_path.is_file():
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        run(compile_command, dry_run=dry_run)
    run([uv, "venv", "--python", str(spec["python"]), str(venv)], dry_run=dry_run)
    run([uv, "pip", "sync", "--python", str(venv_python(venv)), str(lock_path)], dry_run=dry_run)


def install_cli(spec: dict) -> None:
    command = spec["command"]
    if shutil.which(command[0]) is None:
        raise InstallError(f"CLI is not installed: {command[0]}")
    result = subprocess.run(command, env=environment(), capture_output=True, text=True, check=False)
    output = (result.stdout or result.stderr).strip().splitlines()
    if result.returncode:
        raise InstallError(f"CLI version command failed: {command_text(command)}")
    print(output[0] if output else command_text(command))


def install_agent(spec: dict, *, dry_run: bool) -> None:
    kind = spec["kind"]
    if kind == "uv_project":
        install_uv_project(spec, dry_run=dry_run)
    elif kind in {"requirements", "pinned_requirements"}:
        install_requirements(spec, dry_run=dry_run)
    elif kind == "cli":
        if dry_run:
            print(command_text(spec["command"]))
        else:
            install_cli(spec)
    else:
        raise InstallError(f"unsupported agent environment kind: {kind}")


def profile_names(manifest: dict) -> list[str]:
    return [
        f"{benchmark}.{agent}"
        for benchmark in manifest["benchmarks"]
        for agent in manifest["agents"]
    ]


def install_profile(manifest: dict, profile: str, *, dry_run: bool) -> None:
    try:
        benchmark_name, agent_name = profile.split(".", 1)
        benchmark = manifest["benchmarks"][benchmark_name]
        agent = manifest["agents"][agent_name]
    except (ValueError, KeyError) as exc:
        valid = ", ".join(profile_names(manifest))
        raise InstallError(f"unknown profile {profile!r}; choose one of: {valid}") from exc

    print(f"[{profile}] benchmark environment")
    install_uv_project(benchmark, dry_run=dry_run)
    print(f"[{profile}] agent environment")
    install_agent(agent, dry_run=dry_run)


def main(argv: list[str] | None = None) -> int:
    manifest = load_manifest()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", nargs="?", choices=[*profile_names(manifest), "all"])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--list", action="store_true", help="list all benchmark/agent profiles")
    args = parser.parse_args(argv)
    if args.list:
        print("\n".join(profile_names(manifest)))
        return 0
    if args.profile is None:
        parser.error("provide a profile or use --list")

    profiles = profile_names(manifest) if args.profile == "all" else [args.profile]
    completed: set[str] = set()
    for profile in profiles:
        if profile in completed:
            continue
        install_profile(manifest, profile, dry_run=args.dry_run)
        completed.add(profile)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except InstallError as exc:
        print(f"environment install failed: {exc}", file=sys.stderr)
        raise SystemExit(2)
