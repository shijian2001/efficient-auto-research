"""Host-owned hardware and locked-environment lease for Optimizer Design."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import resource
import shutil
import subprocess
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Iterator

from ..contracts import AdapterError
from .protocol import OptimizerDesignProtocol
from .protocol import EnvironmentManifest
from .runtime import directory_digest, python_package_fingerprint


@contextmanager
def _gpu_lock(gpu_id: str) -> Iterator[None]:
    lock_path = Path(f"/tmp/efficient-auto-research-gpu-{gpu_id}.lock")
    with lock_path.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise AdapterError(f"Optimizer Design GPU {gpu_id} is already reserved") from exc
        handle.seek(0)
        handle.truncate()
        handle.write(f"pid={os.getpid()}\n")
        handle.flush()
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _locked_environment(
    protocol: OptimizerDesignProtocol,
    environment_python: Path,
) -> dict[str, object]:
    requested_python = environment_python.expanduser().absolute()
    if not requested_python.is_file() or not os.access(requested_python, os.X_OK):
        raise AdapterError(f"Optimizer Design locked Python is unavailable: {requested_python}")
    uv = shutil.which("uv")
    if uv is None:
        raise AdapterError("Optimizer Design environment validation requires uv")
    environment_root = requested_python.parent.parent
    environment = os.environ.copy()
    environment.update(
        {
            "UV_PROJECT_ENVIRONMENT": str(environment_root),
            "UV_NO_SYNC": "1",
        }
    )
    completed = subprocess.run(
        [
            uv,
            "sync",
            "--project",
            str(protocol.environment_lock_path.parent),
            "--python",
            str(requested_python),
            "--locked",
            "--offline",
            "--dry-run",
        ],
        cwd=protocol.environment_lock_path.parent,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode:
        detail = (completed.stderr or completed.stdout).strip().splitlines()
        raise AdapterError(
            "Optimizer Design Python environment does not satisfy the frozen UV lock"
            + (f": {detail[-1]}" if detail else "")
        )
    probe = subprocess.run(
        [
            str(requested_python),
            "-c",
            (
                "import json, platform, torch; "
                "print(json.dumps({'python': platform.python_version(), "
                "'torch': torch.__version__, 'cuda': torch.version.cuda}))"
            ),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if probe.returncode:
        raise AdapterError("Optimizer Design locked Python probe failed")
    versions = json.loads(probe.stdout)
    resolved_python = requested_python.resolve()
    manifest = EnvironmentManifest.load(protocol.environment_manifest_path)
    environment_sha256 = directory_digest(environment_root)
    package_fingerprint = python_package_fingerprint(requested_python)
    if (
        environment_sha256 != manifest.environment_sha256
        or package_fingerprint != manifest.package_fingerprint
        or hashlib.sha256(resolved_python.read_bytes()).hexdigest()
        != manifest.python_executable_sha256
    ):
        raise AdapterError("Optimizer Design locked environment content drift")
    return {
        "environment_python": str(requested_python),
        "environment_python_resolved": str(resolved_python),
        "environment_python_sha256": manifest.python_executable_sha256,
        "environment_sha256": environment_sha256,
        "environment_package_fingerprint": package_fingerprint,
        "environment_root": str(environment_root),
        "python_version": versions["python"],
        "torch_version": versions["torch"],
        "cuda_runtime": versions["cuda"],
        "uv_lock_digest": protocol.environment_lock_digest,
        "uv_locked_offline_dry_run": True,
    }


def _hardware(gpu_ids: tuple[str, ...]) -> dict[str, object]:
    observed: list[dict[str, object]] = []
    selected_uuids: set[str] = set()
    for gpu_id in gpu_ids:
        completed = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=index,name,uuid,memory.total,memory.used,driver_version,compute_mode",
                "--format=csv,noheader,nounits",
                "--id",
                gpu_id,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        rows = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
        if completed.returncode or len(rows) != 1:
            raise AdapterError(f"cannot inspect Optimizer Design GPU {gpu_id}")
        fields = [field.strip() for field in rows[0].split(",")]
        if len(fields) != 7:
            raise AdapterError("unexpected nvidia-smi output for Optimizer Design GPU")
        index, name, gpu_uuid, total_text, used_text, driver, compute_mode = fields
        try:
            memory_total_mb = int(float(total_text))
            memory_used_mb = int(float(used_text))
        except ValueError as exc:
            raise AdapterError("cannot parse Optimizer Design GPU memory") from exc
        if index != gpu_id or "H100" not in name or memory_total_mb < 75000:
            raise AdapterError("Optimizer Design reconstruction requires four H100 80GB GPUs")
        if memory_used_mb > 1024:
            raise AdapterError(f"Optimizer Design GPU {gpu_id} is not exclusive")
        selected_uuids.add(gpu_uuid)
        observed.append(
            {
                "gpu_id": gpu_id,
                "gpu_name": name,
                "gpu_uuid": gpu_uuid,
                "gpu_memory_total_mb": memory_total_mb,
                "gpu_memory_used_mb_before_run": memory_used_mb,
                "driver_version": driver,
                "compute_mode": compute_mode,
            }
        )
    processes = subprocess.run(
        [
            "nvidia-smi",
            "--query-compute-apps=gpu_uuid,pid,used_gpu_memory",
            "--format=csv,noheader,nounits",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if processes.returncode:
        raise AdapterError("cannot inspect existing Optimizer Design GPU processes")
    occupants = [
        line.strip()
        for line in processes.stdout.splitlines()
        if line.strip() and line.split(",", 1)[0].strip() in selected_uuids
    ]
    if occupants:
        raise AdapterError("one or more Optimizer Design GPUs have existing compute processes")
    return {
        "gpu_count": len(observed),
        "gpus": observed,
        "gpu_exclusivity": "verified-and-host-locked",
    }


def _parse_cpu_set(value: str) -> set[int]:
    cpus: set[int] = set()
    for item in value.split(","):
        item = item.strip()
        if not item:
            raise AdapterError("Optimizer Design CPU set contains an empty item")
        if "-" in item:
            start_text, end_text = item.split("-", 1)
            if not start_text.isdigit() or not end_text.isdigit():
                raise AdapterError("Optimizer Design CPU set range is invalid")
            start, end = int(start_text), int(end_text)
            if end < start:
                raise AdapterError("Optimizer Design CPU set range is reversed")
            cpus.update(range(start, end + 1))
        elif item.isdigit():
            cpus.add(int(item))
        else:
            raise AdapterError("Optimizer Design CPU set item is invalid")
    if not cpus:
        raise AdapterError("Optimizer Design CPU set must not be empty")
    return cpus


@contextmanager
def _resource_limits(
    cpu_set: str | None,
    memory_limit_gib: int | None,
) -> Iterator[dict[str, object]]:
    if cpu_set is None or memory_limit_gib is None:
        raise AdapterError("Optimizer Design runs require --cpu-set and --memory-limit-gib")
    if memory_limit_gib < 64:
        raise AdapterError("Optimizer Design memory limit must be at least 64 GiB")
    requested_cpus = _parse_cpu_set(cpu_set)
    original_cpus = set(os.sched_getaffinity(0))
    if not requested_cpus.issubset(original_cpus):
        raise AdapterError("requested Optimizer Design CPU set is outside the current cpuset")
    original_limit = resource.getrlimit(resource.RLIMIT_AS)
    memory_limit_bytes = memory_limit_gib * 1024**3
    hard_limit = original_limit[1]
    if hard_limit != resource.RLIM_INFINITY and memory_limit_bytes > hard_limit:
        raise AdapterError("requested Optimizer Design memory limit exceeds the hard process limit")
    os.sched_setaffinity(0, requested_cpus)
    try:
        resource.setrlimit(resource.RLIMIT_AS, (memory_limit_bytes, hard_limit))
    except (OSError, ValueError) as exc:
        os.sched_setaffinity(0, original_cpus)
        raise AdapterError("could not enforce the Optimizer Design RAM limit") from exc
    memory_total_kib = None
    for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
        if line.startswith("MemTotal:"):
            memory_total_kib = int(line.split()[1])
            break
    try:
        yield {
            "cpu_affinity": sorted(requested_cpus),
            "cpu_count": len(requested_cpus),
            "memory_limit_gib": memory_limit_gib,
            "rlimit_as_bytes": memory_limit_bytes,
            "memory_total_kib": memory_total_kib,
        }
    finally:
        resource.setrlimit(resource.RLIMIT_AS, original_limit)
        os.sched_setaffinity(0, original_cpus)


@contextmanager
def optimizer_design_resource_lease(
    *,
    protocol: OptimizerDesignProtocol,
    gpu_ids: tuple[str, ...],
    environment_python: Path,
    cpu_set: str | None,
    memory_limit_gib: int | None,
) -> Iterator[dict[str, object]]:
    if (
        len(gpu_ids) != protocol.gpu_count
        or len(set(gpu_ids)) != protocol.gpu_count
        or any(not gpu_id.isdigit() for gpu_id in gpu_ids)
    ):
        raise AdapterError("Optimizer Design requires four unique numeric physical GPU IDs")
    with ExitStack() as stack:
        limits = stack.enter_context(_resource_limits(cpu_set, memory_limit_gib))
        for gpu_id in sorted(gpu_ids, key=int):
            stack.enter_context(_gpu_lock(gpu_id))
        hardware = _hardware(gpu_ids)
        hardware.update(_locked_environment(protocol, environment_python))
        hardware.update(limits)
        yield hardware


__all__ = ["optimizer_design_resource_lease"]
