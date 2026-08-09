"""Cooperative exclusive GPU allocation and UUID attestation."""

from __future__ import annotations

import fcntl
import os
import subprocess
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Iterator

from .contracts import AdapterError


@contextmanager
def gpu_allocation(
    gpu_ids: tuple[str, ...],
    *,
    expected_type: str,
    gpus_per_evaluation: int,
    max_concurrent_evaluations: int,
) -> Iterator[dict[str, object]]:
    if (
        not gpu_ids
        or len(set(gpu_ids)) != len(gpu_ids)
        or any(not gpu_id.isdigit() for gpu_id in gpu_ids)
    ):
        raise AdapterError("GPU allocation requires unique numeric physical GPU IDs")
    required = gpus_per_evaluation * max_concurrent_evaluations
    if len(gpu_ids) != required:
        raise AdapterError(
            f"GPU allocation requires exactly {required} GPUs for "
            f"{gpus_per_evaluation} per evaluation × {max_concurrent_evaluations} concurrent"
        )
    with ExitStack() as stack:
        for gpu_id in sorted(gpu_ids, key=int):
            lock_path = Path(f"/tmp/efficient-auto-research-gpu-{gpu_id}.lock")
            handle = stack.enter_context(lock_path.open("a+", encoding="utf-8"))
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise AdapterError(f"GPU {gpu_id} is already reserved") from exc
            handle.seek(0)
            handle.truncate()
            handle.write(f"pid={os.getpid()}\n")
            handle.flush()
            stack.callback(fcntl.flock, handle.fileno(), fcntl.LOCK_UN)
        completed = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=index,name,uuid,memory.total",
                "--format=csv,noheader,nounits",
                "--id",
                ",".join(gpu_ids),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        rows = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
        if completed.returncode or len(rows) != len(gpu_ids):
            raise AdapterError("cannot attest the selected GPU allocation")
        gpus = []
        for row in rows:
            index, name, uuid, memory = [value.strip() for value in row.split(",")]
            if expected_type.lower() not in name.lower():
                raise AdapterError(
                    f"GPU {index} type differs from profile: expected {expected_type}, got {name}"
                )
            gpus.append(
                {
                    "gpu_id": index,
                    "gpu_name": name,
                    "gpu_uuid": uuid,
                    "gpu_memory_total_mb": int(float(memory)),
                }
            )
        yield {
            "gpu_type": expected_type,
            "gpu_count": len(gpus),
            "gpu_ids": list(gpu_ids),
            "gpus": gpus,
            "gpus_per_evaluation": gpus_per_evaluation,
            "max_concurrent_evaluations": max_concurrent_evaluations,
            "gpu_exclusivity": "verified-and-host-locked",
        }


__all__ = ["gpu_allocation"]
