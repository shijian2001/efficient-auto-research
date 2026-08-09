"""CLI for the shared benchmark adapter packages."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import resource
import shlex
import shutil
import subprocess
from contextlib import contextmanager
from pathlib import Path

from .agents import get_agent_adapter
from .AutoResearch.aggregate import aggregate_autoresearch, autoresearch_scorecard
from .AutoResearch.baseline import KernelCacheManifest, PreparedAssetManifest
from .AutoResearch.baseline_run import run_autoresearch_baseline
from .AutoResearch.evaluator import CandidateEvaluator, EvaluatorManifest
from .AutoResearch.launchers import NativeCommandSearchRunner, NativeLaunchRequest, build_native_command
from .AutoResearch.model_adapters import model_identity
from .AutoResearch.protocol import AutoResearchProtocol, build_protocol as build_autoresearch_protocol
from .AutoResearch.supervisor import run_autoresearch
from .contracts import AdapterError, CommandSpec
from .baseline_maintenance import promote_baseline_record
from .freeze_maintenance import (
    freeze_implementation_manifest_candidate,
    freeze_terminal_dataset_candidate,
)
from .FMLBench.adapter import FMLBenchmarkAdapter, FMLRunRequest
from .FMLBench.aggregate import aggregate_fml, fml_scorecard
from .FMLBench.audit import audit_report, build_review_candidate
from .FMLBench.protocol import FMLProtocol
from .FMLBench.readiness import collect_fml_readiness
from .FMLBench.runner import run_fml_task
from .formal_contract import ModelTrackConfig
from .formal_preflight import collect_formal_preflight
from .MLEBenchLite.campaign import (
    aggregate_campaign,
    build_mle_protocol,
    campaign_cells,
    mle_scorecard,
    run_campaign_cell,
)
from .MLEBenchLite.adapter import MleLiteRequest
from .MLEBenchLite.membership import freeze_data_manifest
from .OptimizerDesign.adapter import OptimizerDesignRequest
from .OptimizerDesign.aggregate import aggregate_optimizer_design, optimizer_design_scorecard
from .OptimizerDesign.baseline import run_optimizer_design_baseline
from .OptimizerDesign.protocol import (
    DEFAULT_DATA_ROOT as DEFAULT_OPTIMIZER_DESIGN_DATA,
    DEFAULT_ENVIRONMENT_PYTHON as DEFAULT_OPTIMIZER_DESIGN_PYTHON,
    DEFAULT_SOURCE_ROOT as DEFAULT_OPTIMIZER_DESIGN_SOURCE,
    OptimizerDesignProtocol,
    SourceManifest,
    build_protocol as build_optimizer_design_protocol,
)
from .OptimizerDesign.runtime import freeze_agent_runtime_manifest_candidate
from .preflight import collect_preflight
from .process import DEFAULT_PROXY, relay_client_env, run_command
from .protocol import FormalProtocol, write_json_exclusive
from .registry import AGENTS, ROOT
from .security import is_sensitive_name, redact_url
from .status import collect_status
from .TerminalBench.adapter import HarborTerminalRequest
from .TerminalAO.adapter import TerminalAORequest
from .TerminalAO.aggregate import aggregate_terminal_ao, terminal_ao_scorecard
from .TerminalAO.protocol import TerminalAOProtocol, build_protocol_candidate as build_terminal_ao_protocol

def _redacted_argv(argv: tuple[str, ...]) -> tuple[str, ...]:
    redacted: list[str] = []
    redact_next = False
    for value in argv:
        value = re.sub(r"(--token(?:=|\s+))\S+", r"\1<redacted>", value)
        if redact_next:
            redacted.append("<redacted>")
            redact_next = False
            continue
        name, separator, _ = value.partition("=")
        if separator and is_sensitive_name(name):
            redacted.append(f"{name}=<redacted>")
        else:
            redacted.append(value)
            redact_next = value.startswith("-") and is_sensitive_name(value.lstrip("-"))
    return tuple(redacted)


def _command_payload(command: CommandSpec) -> dict[str, object]:
    return {
        "command": shlex.join(_redacted_argv(command.argv)),
        "cwd": str(command.cwd),
        "environment": {
            key: redact_url(value)
            for key, value in sorted(command.env.items())
            if not is_sensitive_name(key)
        },
        "timeout_seconds": command.timeout_seconds,
        "label": command.label,
        "artifact_path": str(command.artifact_path) if command.artifact_path else None,
    }


def _add_common_endpoint_args(
    parser: argparse.ArgumentParser,
    *,
    include_timeout: bool = True,
) -> None:
    parser.add_argument("--model")
    parser.add_argument(
        "--upstream-base-url",
        default="",
    )
    parser.add_argument("--proxy", default=DEFAULT_PROXY)
    if include_timeout:
        parser.add_argument("--timeout", type=int, default=900)


def _mle_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("mle", help="build or run an MLE-Bench Lite adapter")
    parser.add_argument("--agent", required=True)
    parser.add_argument("--competition-id", required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--gpu-id", type=int, default=0)
    parser.add_argument("--steps", type=int, default=1)
    parser.add_argument("--run-tag")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--max-turns", type=int, default=8)
    parser.add_argument("--instruction")
    parser.add_argument("--config-path", type=Path)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    _add_common_endpoint_args(parser)


def _terminal_parser(
    subparsers: argparse._SubParsersAction,
    name: str,
    *,
    help_text: str,
) -> None:
    parser = subparsers.add_parser(name, help=help_text)
    parser.add_argument("--agent", required=True)
    parser.add_argument(
        "--dataset-dir",
        type=Path,
        default=HarborTerminalRequest.__dataclass_fields__["dataset_dir"].default,
    )
    parser.add_argument(
        "--jobs-dir",
        type=Path,
        default=HarborTerminalRequest.__dataclass_fields__["jobs_dir"].default,
    )
    parser.add_argument("--attempts", type=int, default=1)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--agent-concurrency", type=int)
    parser.add_argument("--task", action="append", default=[])
    parser.add_argument("--exclude-task", action="append", default=[])
    parser.add_argument("--agent-kwarg", action="append", default=[])
    parser.add_argument("--job-name")
    parser.add_argument("--max-retries", type=int, default=0)
    parser.add_argument("--timeout-multiplier", type=float, default=1.0)
    parser.add_argument("--force-build", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    _add_common_endpoint_args(parser, include_timeout=False)


def _terminal_ao_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "terminal-ao",
        help="run the formal Terminal-Bench 36/53 harness-optimization protocol",
    )
    parser.add_argument("--agent", required=True)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--timeout", type=int, default=172800)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--model-config", type=Path, required=True)
    parser.add_argument("--agent-variant", required=True)
    parser.add_argument("--gpu-id", action="append", default=[])
    _add_common_endpoint_args(parser, include_timeout=False)


def _autoresearch_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "autoresearch",
        help="run or inspect one Autoresearch Architecture Design Agent × seed cell",
    )
    parser.add_argument("--agent", choices=tuple(AGENTS), required=True)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--prepared-root", type=Path, default=ROOT / "autoresearch/.runtime/home/.cache/autoresearch")
    parser.add_argument("--environment-python", type=Path)
    parser.add_argument("--kernel-cache-root", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--gpu-id")
    parser.add_argument("--cpu-set", default=os.environ.get("AUTORESEARCH_CPU_SET"))
    parser.add_argument(
        "--memory-limit-gib",
        type=int,
        default=(
            int(os.environ["AUTORESEARCH_MEMORY_LIMIT_GIB"])
            if os.environ.get("AUTORESEARCH_MEMORY_LIMIT_GIB")
            else None
        ),
    )
    parser.add_argument("--dry-run", action="store_true")
    run_mode = parser.add_mutually_exclusive_group()
    run_mode.add_argument("--smoke", action="store_true")
    run_mode.add_argument("--pilot", action="store_true")
    parser.add_argument("--outer-budget-seconds", type=int)
    parser.add_argument("--native-step-limit", type=int)
    parser.add_argument("--readiness-evidence", type=Path)
    parser.add_argument("--model-config", type=Path, required=True)
    parser.add_argument("--agent-variant", required=True)
    _add_common_endpoint_args(parser, include_timeout=False)


def _optimizer_design_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "optimizer-design",
        help="run or inspect one modded-NanoGPT Track 3 Agent × seed cell",
    )
    parser.add_argument("--agent", choices=tuple(AGENTS), required=True)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, default=DEFAULT_OPTIMIZER_DESIGN_SOURCE)
    parser.add_argument("--data-root", type=Path, default=DEFAULT_OPTIMIZER_DESIGN_DATA)
    parser.add_argument(
        "--environment-python",
        type=Path,
        default=DEFAULT_OPTIMIZER_DESIGN_PYTHON,
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--gpu-id", action="append", default=[])
    parser.add_argument("--cpu-set", default=os.environ.get("OPTIMIZER_DESIGN_CPU_SET"))
    parser.add_argument(
        "--memory-limit-gib",
        type=int,
        default=(
            int(os.environ["OPTIMIZER_DESIGN_MEMORY_LIMIT_GIB"])
            if os.environ.get("OPTIMIZER_DESIGN_MEMORY_LIMIT_GIB")
            else None
        ),
    )
    parser.add_argument("--dry-run", action="store_true")
    run_mode = parser.add_mutually_exclusive_group()
    run_mode.add_argument("--smoke", action="store_true")
    run_mode.add_argument("--pilot", action="store_true")
    parser.add_argument("--outer-budget-seconds", type=int)
    parser.add_argument("--native-step-limit", type=int)
    parser.add_argument("--model-config", type=Path, required=True)
    parser.add_argument("--agent-variant", required=True)
    _add_common_endpoint_args(parser, include_timeout=False)


def _fml_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "fml",
        help="run one pinned upstream FML task cell through the formal thin adapter",
    )
    parser.add_argument("--agent", choices=tuple(AGENTS), required=True)
    parser.add_argument("--agent-variant", required=True)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--task-config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--outer-run-index", type=int, required=True)
    parser.add_argument("--model-config", type=Path, required=True)
    parser.add_argument(
        "--credential-env",
        action="append",
        default=[],
        help="name of one required credential environment variable; values are never serialized",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--smoke", action="store_true")
    mode.add_argument("--dry-run", action="store_true")
    parser.add_argument("--gpu-id", action="append", default=[])
    review = subparsers.add_parser(
        "fml-review-protocol",
        help="generate a non-promoting 18-task FML protocol review candidate",
    )
    review.add_argument("--upstream-root", type=Path, required=True)
    review.add_argument("--output", type=Path, required=True)
    readiness = subparsers.add_parser(
        "fml-readiness",
        help="report evidence-based readiness for all seven FML adapters",
    )
    readiness.add_argument("--protocol", type=Path)
    readiness.add_argument("--formal-evidence-root", type=Path)
    readiness.add_argument("--output", type=Path)


def _autoresearch_model_environment(
    *,
    model_config: ModelTrackConfig,
    proxy: str,
    include_credentials: bool,
    task_name: str = "Autoresearch",
) -> dict[str, str]:
    model_config.validate(formal=include_credentials)
    environment = relay_client_env(
        base_url=model_config.relay_base_url,
        proxy=proxy,
        model=model_config.outer_model_id,
        include_credentials=include_credentials,
    )
    environment["AUTORESEARCH_MODEL"] = model_config.outer_model_id
    environment["AUTORESEARCH_MODEL_PARAMETERS"] = json.dumps(
        model_config.model_parameters, sort_keys=True
    )
    environment["AUTORESEARCH_REQUEST_TIMEOUT_SECONDS"] = (
        ""
        if model_config.request_timeout_seconds is None
        else str(model_config.request_timeout_seconds)
    )
    environment["AUTORESEARCH_RETRY_POLICY"] = json.dumps(
        model_config.retry_policy, sort_keys=True
    )
    environment["AUTORESEARCH_CODEX_BASE_URL"] = model_config.relay_base_url.rstrip("/")
    from .task_specs import task_spec_digest, task_spec_text

    benchmark_id = "optimizer-design" if task_name == "Optimizer Design" else "autoresearch-architecture"
    environment["BENCHMARK_TASK_SPEC_TEXT"] = task_spec_text(benchmark_id)
    environment["BENCHMARK_TASK_SPEC_SHA256"] = task_spec_digest(benchmark_id)
    if include_credentials and not environment.get("OPENAI_API_KEY"):
        raise AdapterError(f"formal {task_name} requires OPENAI_API_KEY or UPSTREAM_API_KEY")
    return environment


def _formal_autoresearch_hardware(
    gpu_id: str | None,
    *,
    require_exclusive: bool = True,
) -> dict[str, object]:
    if gpu_id is None:
        raise AdapterError("formal Autoresearch runs require an explicit --gpu-id")
    if not gpu_id.isdigit():
        raise AdapterError("formal Autoresearch --gpu-id must be a numeric physical GPU index")
    completed = subprocess.run(
        [
            "nvidia-smi",
            "--query-gpu=name,uuid,memory.total,memory.used,driver_version,compute_mode",
            "--format=csv,noheader,nounits",
            "--id",
            gpu_id,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode:
        raise AdapterError(f"cannot inspect formal Autoresearch GPU {gpu_id}")
    rows = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    if len(rows) != 1:
        raise AdapterError("formal Autoresearch run must resolve exactly one exclusive GPU")
    fields = [field.strip() for field in rows[0].split(",")]
    if len(fields) != 6:
        raise AdapterError("unexpected nvidia-smi output for formal Autoresearch GPU")
    name, gpu_uuid, memory_mb, memory_used_mb, driver, compute_mode = fields
    try:
        memory_total_mb = int(float(memory_mb))
        memory_used = int(float(memory_used_mb))
    except ValueError as exc:
        raise AdapterError("cannot parse formal Autoresearch GPU memory") from exc
    if "H100" not in name or memory_total_mb < 75000:
        raise AdapterError("formal Autoresearch protocol requires one H100 80GB GPU")
    compute_processes = subprocess.run(
        [
            "nvidia-smi",
            "--query-compute-apps=gpu_uuid,pid,used_gpu_memory",
            "--format=csv,noheader,nounits",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if compute_processes.returncode:
        raise AdapterError("cannot inspect existing GPU compute processes")
    occupants = [
        line.strip()
        for line in compute_processes.stdout.splitlines()
        if line.strip().split(",", 1)[0].strip() == gpu_uuid
    ]
    if require_exclusive and (occupants or memory_used > 1024):
        raise AdapterError(f"formal Autoresearch GPU {gpu_id} is not exclusive")
    memory_limit_path = Path("/sys/fs/cgroup/memory.max")
    memory_limit_raw = (
        memory_limit_path.read_text(encoding="utf-8").strip()
        if memory_limit_path.is_file()
        else "unavailable"
    )
    memory_total_kib = None
    for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
        if line.startswith("MemTotal:"):
            memory_total_kib = int(line.split()[1])
            break
    return {
        "gpu_id": gpu_id,
        "gpu_name": name,
        "gpu_uuid": gpu_uuid,
        "gpu_memory_total_mb": memory_total_mb,
        "gpu_memory_used_mb_before_run": memory_used,
        "driver_version": driver,
        "compute_mode": compute_mode,
        "cpu_affinity": sorted(os.sched_getaffinity(0)),
        "cpu_count": len(os.sched_getaffinity(0)),
        "memory_total_kib": memory_total_kib,
        "cgroup_memory_max": memory_limit_raw,
        "rlimit_as_bytes": resource.getrlimit(resource.RLIMIT_AS)[0],
        "gpu_exclusivity": (
            "verified no existing compute process and guarded by host lock"
            if require_exclusive
            else "not checked by credential-free environment verification"
        ),
    }


@contextmanager
def _exclusive_gpu_lock(gpu_id: str, *, task_name: str = "Autoresearch"):
    lock_path = Path(f"/tmp/efficient-auto-research-gpu-{gpu_id}.lock")
    with lock_path.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise AdapterError(f"{task_name} GPU {gpu_id} is already reserved") from exc
        handle.seek(0)
        handle.truncate()
        handle.write(f"pid={os.getpid()}\n")
        handle.flush()
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _parse_cpu_set(value: str) -> set[int]:
    cpus: set[int] = set()
    for item in value.split(","):
        item = item.strip()
        if not item:
            raise AdapterError("Autoresearch CPU set contains an empty item")
        if "-" in item:
            start_text, end_text = item.split("-", 1)
            if not start_text.isdigit() or not end_text.isdigit():
                raise AdapterError("Autoresearch CPU set range is invalid")
            start, end = int(start_text), int(end_text)
            if end < start:
                raise AdapterError("Autoresearch CPU set range is reversed")
            cpus.update(range(start, end + 1))
        elif item.isdigit():
            cpus.add(int(item))
        else:
            raise AdapterError("Autoresearch CPU set item is invalid")
    if not cpus:
        raise AdapterError("Autoresearch CPU set must not be empty")
    return cpus


def _apply_autoresearch_resource_limits(cpu_set: str | None, memory_limit_gib: int | None) -> None:
    if not cpu_set or memory_limit_gib is None:
        raise AdapterError(
            "formal Autoresearch runs require --cpu-set and --memory-limit-gib"
        )
    if memory_limit_gib < 64:
        raise AdapterError("formal Autoresearch memory limit must be at least 64 GiB")
    requested_cpus = _parse_cpu_set(cpu_set)
    available_cpus = set(os.sched_getaffinity(0))
    if not requested_cpus.issubset(available_cpus):
        raise AdapterError("requested Autoresearch CPU set is outside the current cpuset")
    os.sched_setaffinity(0, requested_cpus)
    if set(os.sched_getaffinity(0)) != requested_cpus:
        raise AdapterError("could not enforce the requested Autoresearch CPU set")
    memory_limit_bytes = memory_limit_gib * 1024**3
    try:
        resource.setrlimit(resource.RLIMIT_AS, (memory_limit_bytes, memory_limit_bytes))
    except (OSError, ValueError) as exc:
        raise AdapterError("could not enforce the Autoresearch RAM limit") from exc
    if resource.getrlimit(resource.RLIMIT_AS)[0] != memory_limit_bytes:
        raise AdapterError("Autoresearch RAM limit attestation failed")


def _formal_autoresearch_environment(
    protocol: AutoResearchProtocol,
    environment_python: Path,
) -> dict[str, object]:
    requested_python = environment_python.expanduser().absolute()
    if not requested_python.is_file() or not os.access(requested_python, os.X_OK):
        raise AdapterError(f"Autoresearch locked Python is unavailable: {requested_python}")
    environment_root = requested_python.parent.parent
    environment_python = requested_python.resolve()
    uv = shutil.which("uv")
    if uv is None:
        raise AdapterError("formal Autoresearch environment validation requires uv")
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
            str(protocol.source_root),
            "--python",
            str(environment_python),
            "--locked",
            "--offline",
            "--dry-run",
        ],
        cwd=protocol.source_root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode:
        detail = (completed.stderr or completed.stdout).strip().splitlines()
        raise AdapterError(
            "Autoresearch Python environment does not satisfy the frozen UV lock"
            + (f": {detail[-1]}" if detail else "")
        )
    version = subprocess.run(
        [str(requested_python), "-c", "import platform; print(platform.python_version())"],
        capture_output=True,
        text=True,
        check=False,
    )
    if version.returncode or not version.stdout.strip().startswith("3.10."):
        raise AdapterError("formal Autoresearch protocol requires the locked Python 3.10 runtime")
    digest = hashlib.sha256(environment_python.read_bytes()).hexdigest()
    return {
        "environment_python": str(requested_python),
        "environment_python_resolved": str(environment_python),
        "environment_root": str(environment_root),
        "environment_python_sha256": digest,
        "python_version": version.stdout.strip(),
        "uv_lock_digest": protocol.environment_lock_digest,
        "uv_locked_offline_dry_run": True,
    }


def _formal_tools_parsers(subparsers: argparse._SubParsersAction) -> None:
    status = subparsers.add_parser("status", help="show stable protocol/readiness status")
    status.add_argument("--output", type=Path)

    preflight = subparsers.add_parser("preflight", help="validate formal protocols and readiness")
    preflight.add_argument("--mle-data-root", type=Path, default=ROOT / "mle-bench-data")
    preflight.add_argument(
        "--ao-protocol",
        type=Path,
        default=ROOT / "terminal-bench-2/ao_protocol/protocol.json",
    )
    preflight.add_argument(
        "--autoresearch-protocol",
        type=Path,
        default=ROOT / "autoresearch/protocol/protocol.json",
    )
    preflight.add_argument(
        "--autoresearch-prepared-root",
        type=Path,
        default=ROOT / "autoresearch/.runtime/home/.cache/autoresearch",
    )
    preflight.add_argument(
        "--autoresearch-kernel-cache-root",
        type=Path,
        default=(
            ROOT
            / "autoresearch/.runtime/home/.cache/huggingface/hub/models--varunneal--flash-attention-3"
        ),
    )
    preflight.add_argument(
        "--autoresearch-environment-python",
        type=Path,
        default=ROOT / "autoresearch/.venv/bin/python",
    )
    preflight.add_argument(
        "--optimizer-design-protocol",
        type=Path,
        default=ROOT / "optimizer-design/protocol/protocol.json",
    )
    preflight.add_argument(
        "--optimizer-design-source-root",
        type=Path,
        default=DEFAULT_OPTIMIZER_DESIGN_SOURCE,
    )
    preflight.add_argument(
        "--optimizer-design-data-root",
        type=Path,
        default=DEFAULT_OPTIMIZER_DESIGN_DATA,
    )
    preflight.add_argument(
        "--optimizer-design-environment-python",
        type=Path,
        default=DEFAULT_OPTIMIZER_DESIGN_PYTHON,
    )

    protocol = subparsers.add_parser("mle-protocol", help="write the frozen MLE protocol")
    protocol.add_argument("--output", type=Path, required=True)
    protocol.add_argument("--seed", action="append", type=int, default=[])
    protocol.add_argument("--outer-repetitions", type=int, choices=(1, 3), default=1)
    protocol.add_argument("--timeout", type=int, default=86400)

    mle_freeze = subparsers.add_parser(
        "mle-freeze-assets",
        help="explicitly generate a reviewed MLE prepared/archive/grader asset manifest",
    )
    mle_freeze.add_argument("--data-root", type=Path, required=True)
    mle_freeze.add_argument("--output", type=Path, required=True)

    cell = subparsers.add_parser("mle-cell", help="run one manifest-bound formal MLE cell")
    cell.add_argument("--protocol", type=Path, required=True)
    cell.add_argument("--agent", required=True)
    cell.add_argument("--competition-id", required=True)
    cell.add_argument("--seed", type=int, required=True)
    cell.add_argument("--data-root", type=Path, required=True)
    cell.add_argument("--campaign-dir", type=Path, required=True)
    cell.add_argument("--gpu-id", type=int, default=0)
    cell.add_argument("--model-config", type=Path, required=True)
    cell.add_argument("--agent-variant", required=True)

    mle_aggregate = subparsers.add_parser("mle-aggregate", help="aggregate official MLE reports")
    mle_aggregate.add_argument("--protocol", type=Path, required=True)
    mle_aggregate.add_argument("--campaign-dir", type=Path, required=True)
    mle_aggregate.add_argument("--agent", required=True)
    mle_aggregate.add_argument("--output", type=Path)

    mle_card = subparsers.add_parser("mle-scorecard", help="produce the formal MLE scorecard")
    mle_card.add_argument("--protocol", type=Path, required=True)
    mle_card.add_argument("--campaign-dir", type=Path, required=True)
    mle_card.add_argument("--output", type=Path)

    ao_aggregate = subparsers.add_parser(
        "terminal-ao-aggregate", help="aggregate held-out 53-task AO results"
    )
    ao_aggregate.add_argument("--protocol", type=Path, required=True)
    ao_aggregate.add_argument("--campaign-dir", type=Path, required=True)
    ao_aggregate.add_argument("--agent", required=True)
    ao_aggregate.add_argument("--output", type=Path)

    terminal_card = subparsers.add_parser(
        "terminal-ao-scorecard", help="produce the formal Terminal AO scorecard"
    )
    terminal_card.add_argument("--protocol", type=Path, required=True)
    terminal_card.add_argument("--campaign-dir", type=Path, required=True)
    terminal_card.add_argument("--output", type=Path)

    terminal_protocol = subparsers.add_parser(
        "terminal-ao-protocol",
        help="generate a schema-v2 Terminal AO protocol candidate with explicit source commit",
    )
    terminal_protocol.add_argument("--source", type=Path, required=True)
    terminal_protocol.add_argument("--benchmark-source-commit", required=True)
    terminal_protocol.add_argument("--outer-repetitions", type=int, choices=(1, 3), default=1)
    terminal_protocol.add_argument("--output", type=Path, required=True)

    autoresearch_protocol = subparsers.add_parser(
        "autoresearch-protocol", help="write or validate the frozen Autoresearch protocol"
    )
    autoresearch_protocol_mode = autoresearch_protocol.add_mutually_exclusive_group(required=True)
    autoresearch_protocol_mode.add_argument("--output", type=Path)
    autoresearch_protocol_mode.add_argument("--validate", type=Path)
    autoresearch_protocol.add_argument("--outer-repetitions", type=int, choices=(1, 3), default=1)

    optimizer_design_protocol = subparsers.add_parser(
        "optimizer-design-protocol",
        help="write or validate the frozen Optimizer Design reconstruction protocol",
    )
    optimizer_design_protocol_mode = optimizer_design_protocol.add_mutually_exclusive_group(
        required=True
    )
    optimizer_design_protocol_mode.add_argument("--output", type=Path)
    optimizer_design_protocol_mode.add_argument("--validate", type=Path)
    optimizer_design_protocol.add_argument(
        "--outer-repetitions", type=int, choices=(1, 3), default=1
    )

    optimizer_design_baseline = subparsers.add_parser(
        "optimizer-design-baseline",
        help="run the protected two-seed Optimizer Design baseline",
    )
    optimizer_design_baseline.add_argument("--protocol", type=Path, required=True)
    optimizer_design_baseline.add_argument(
        "--source-root", type=Path, default=DEFAULT_OPTIMIZER_DESIGN_SOURCE
    )
    optimizer_design_baseline.add_argument(
        "--data-root", type=Path, default=DEFAULT_OPTIMIZER_DESIGN_DATA
    )
    optimizer_design_baseline.add_argument(
        "--environment-python", type=Path, default=DEFAULT_OPTIMIZER_DESIGN_PYTHON
    )
    optimizer_design_baseline.add_argument("--gpu-id", action="append", required=True)
    optimizer_design_baseline.add_argument("--cpu-set", required=True)
    optimizer_design_baseline.add_argument("--memory-limit-gib", type=int, required=True)
    optimizer_design_baseline.add_argument("--output-dir", type=Path, required=True)

    autoresearch_baseline = subparsers.add_parser(
        "autoresearch-baseline",
        help="run the protected two-seed H100 Autoresearch baseline and generate a review candidate",
    )
    autoresearch_baseline.add_argument("--protocol", type=Path, required=True)
    autoresearch_baseline.add_argument("--prepared-root", type=Path, required=True)
    autoresearch_baseline.add_argument("--kernel-cache-root", type=Path, required=True)
    autoresearch_baseline.add_argument("--environment-python", type=Path, required=True)
    autoresearch_baseline.add_argument("--gpu-id", required=True)
    autoresearch_baseline.add_argument("--cpu-set", required=True)
    autoresearch_baseline.add_argument("--memory-limit-gib", type=int, required=True)
    autoresearch_baseline.add_argument("--output-dir", type=Path, required=True)

    baseline_verify = subparsers.add_parser(
        "baseline-verify", help="verify a completed baseline candidate and all hashed evidence"
    )
    baseline_verify.add_argument(
        "--benchmark",
        choices=("autoresearch-architecture", "optimizer-design"),
        required=True,
    )
    baseline_verify.add_argument("--candidate", type=Path, required=True)

    baseline_promote = subparsers.add_parser(
        "baseline-promote",
        help="explicitly promote a reviewed completed baseline over a known pending digest",
    )
    baseline_promote.add_argument(
        "--benchmark",
        choices=("autoresearch-architecture", "optimizer-design"),
        required=True,
    )
    baseline_promote.add_argument("--candidate", type=Path, required=True)
    baseline_promote.add_argument("--destination", type=Path, required=True)
    baseline_promote.add_argument("--expected-pending-sha256", required=True)
    baseline_promote.add_argument("--acknowledge-reviewed", action="store_true")

    implementation_freeze = subparsers.add_parser(
        "freeze-implementation-manifest",
        help="generate, but never auto-promote, a protected implementation manifest candidate",
    )
    implementation_freeze.add_argument("--source-manifest", type=Path, required=True)
    implementation_freeze.add_argument("--output", type=Path, required=True)

    runtime_freeze = subparsers.add_parser(
        "optimizer-design-freeze-runtime",
        help="generate, but never auto-promote, the seven-Agent runtime manifest candidate",
    )
    runtime_freeze.add_argument("--source-manifest", type=Path, required=True)
    runtime_freeze.add_argument("--output", type=Path, required=True)

    terminal_freeze = subparsers.add_parser(
        "terminal-ao-freeze-dataset",
        help="generate a reviewed Terminal AO split/dataset digest candidate",
    )
    terminal_freeze.add_argument("--dataset-dir", type=Path, required=True)
    terminal_freeze.add_argument("--source-split", type=Path, required=True)
    terminal_freeze.add_argument("--output", type=Path, required=True)

    optimizer_design_aggregate = subparsers.add_parser(
        "optimizer-design-aggregate",
        help="aggregate one Optimizer Design Agent's configured formal outer runs",
    )
    optimizer_design_aggregate.add_argument("--protocol", type=Path, required=True)
    optimizer_design_aggregate.add_argument("--campaign-dir", type=Path, required=True)
    optimizer_design_aggregate.add_argument("--agent", choices=tuple(AGENTS), required=True)
    optimizer_design_aggregate.add_argument("--output", type=Path)

    optimizer_design_card = subparsers.add_parser(
        "optimizer-design-scorecard",
        help="produce the seven-Agent Optimizer Design scorecard",
    )
    optimizer_design_card.add_argument("--protocol", type=Path, required=True)
    optimizer_design_card.add_argument("--campaign-dir", type=Path, required=True)
    optimizer_design_card.add_argument("--output", type=Path)

    autoresearch_aggregate = subparsers.add_parser(
        "autoresearch-aggregate", help="aggregate one Agent's configured formal outer runs"
    )
    autoresearch_aggregate.add_argument("--protocol", type=Path, required=True)
    autoresearch_aggregate.add_argument("--campaign-dir", type=Path, required=True)
    autoresearch_aggregate.add_argument("--agent", choices=tuple(AGENTS), required=True)
    autoresearch_aggregate.add_argument("--output", type=Path)
    autoresearch_aggregate.add_argument("--readiness-evidence", type=Path)

    autoresearch_card = subparsers.add_parser(
        "autoresearch-scorecard", help="produce the seven-Agent Autoresearch scorecard"
    )
    autoresearch_card.add_argument("--protocol", type=Path, required=True)
    autoresearch_card.add_argument("--campaign-dir", type=Path, required=True)
    autoresearch_card.add_argument("--output", type=Path)

    fml_aggregate = subparsers.add_parser("fml-aggregate", help="aggregate one formal FML Agent")
    fml_aggregate.add_argument("--protocol", type=Path, required=True)
    fml_aggregate.add_argument("--campaign-dir", type=Path, required=True)
    fml_aggregate.add_argument("--agent", choices=tuple(AGENTS), required=True)
    fml_aggregate.add_argument("--output", type=Path)

    fml_card = subparsers.add_parser("fml-scorecard", help="produce the formal FML scorecard")
    fml_card.add_argument("--protocol", type=Path, required=True)
    fml_card.add_argument("--campaign-dir", type=Path, required=True)
    fml_card.add_argument("--output", type=Path)

    formal_preflight = subparsers.add_parser(
        "formal-preflight", help="run one structured fail-closed formal preflight"
    )
    formal_preflight.add_argument(
        "--benchmark",
        choices=(
            "mle-bench-lite",
            "terminal-bench-ao",
            "autoresearch-architecture",
            "optimizer-design",
            "fml-bench",
        ),
        required=True,
    )
    formal_preflight.add_argument("--agent", choices=tuple(AGENTS), required=True)
    formal_preflight.add_argument("--agent-variant", required=True)
    formal_preflight.add_argument("--protocol", type=Path, required=True)
    formal_preflight.add_argument("--model-config", type=Path, required=True)
    formal_preflight.add_argument("--data-root", type=Path)
    formal_preflight.add_argument("--smoke", action="store_true")
    formal_preflight.add_argument("--output", type=Path)

    scorecard = subparsers.add_parser(
        "scorecard", help="produce separate MLE and Terminal AO scorecards"
    )
    scorecard.add_argument("--mle-protocol", type=Path, required=True)
    scorecard.add_argument("--mle-campaign-dir", type=Path, required=True)
    scorecard.add_argument("--ao-protocol", type=Path, required=True)
    scorecard.add_argument("--ao-campaign-dir", type=Path, required=True)
    scorecard.add_argument("--output", type=Path)


def _print_or_write(payload: object, output: Path | None = None) -> None:
    serialized = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    if output is None:
        print(serialized, end="")
        return
    if not isinstance(payload, dict):
        raise AdapterError("immutable JSON output must be an object")
    write_json_exclusive(output.resolve(), payload)


def _handle_formal_tool(args: argparse.Namespace) -> int | None:
    if args.command == "status":
        _print_or_write(collect_status(), args.output)
        return 0
    if args.command == "preflight":
        _print_or_write(
            collect_preflight(
                mle_data_root=args.mle_data_root,
                ao_protocol_path=args.ao_protocol,
                autoresearch_protocol_path=args.autoresearch_protocol,
                autoresearch_prepared_root=args.autoresearch_prepared_root,
                autoresearch_environment_python=args.autoresearch_environment_python,
                autoresearch_kernel_cache_root=args.autoresearch_kernel_cache_root,
                optimizer_design_protocol_path=args.optimizer_design_protocol,
                optimizer_design_source_root=args.optimizer_design_source_root,
                optimizer_design_data_root=args.optimizer_design_data_root,
                optimizer_design_environment_python=args.optimizer_design_environment_python,
            )
        )
        return 0
    if args.command == "mle-protocol":
        repetitions = args.outer_repetitions
        seeds = tuple(args.seed or range(repetitions))
        if len(seeds) != repetitions:
            raise AdapterError("MLE seed/run IDs must match outer_repetitions")
        protocol = build_mle_protocol(
            seeds=seeds,
            wall_clock_seconds=args.timeout,
        )
        protocol.write(args.output)
        _print_or_write({"protocol_path": str(args.output.resolve()), "protocol_digest": protocol.digest})
        return 0
    if args.command == "mle-freeze-assets":
        payload = freeze_data_manifest(data_root=args.data_root, output_path=args.output)
        _print_or_write(
            {
                "action": "generated-for-review",
                "manifest_path": str(args.output.resolve()),
                "manifest_digest": payload["manifest_digest"],
                "promotion_automatic": False,
            }
        )
        return 0
    if args.command == "terminal-ao-protocol":
        protocol = build_terminal_ao_protocol(
            source_path=args.source,
            benchmark_source_commit=args.benchmark_source_commit,
            outer_repetitions=args.outer_repetitions,
        )
        protocol.write(args.output)
        _print_or_write(
            {
                "action": "generated-for-review",
                "protocol_path": str(args.output.resolve()),
                "protocol_digest": protocol.digest,
                "benchmark_source_commit": protocol.benchmark_source_commit,
                "outer_repetitions": protocol.outer_repetitions,
                "automatic_promotion": False,
            }
        )
        return 0
    if args.command == "autoresearch-protocol":
        if args.output is not None:
            protocol = build_autoresearch_protocol(
                outer_repetitions=args.outer_repetitions
            )
            protocol.write(args.output)
            action = "written"
            path = args.output
        else:
            protocol = AutoResearchProtocol.load(args.validate)
            action = "validated"
            path = args.validate
        _print_or_write(
            {
                "action": action,
                "protocol_path": str(path.resolve()),
                "protocol_id": protocol.protocol_id,
                "protocol_digest": protocol.digest,
                "candidate_training_seconds": protocol.candidate_training_seconds,
                "outer_wall_clock_seconds": protocol.outer_wall_clock_seconds,
                "outer_seeds": list(protocol.outer_seeds),
                "held_out_evaluations_per_outer_run": 2,
                "editable_paths": list(protocol.editable_paths),
            }
        )
        return 0
    if args.command == "optimizer-design-protocol":
        if args.output is not None:
            protocol = build_optimizer_design_protocol(
                outer_repetitions=args.outer_repetitions
            )
            protocol.write(args.output)
            action = "written"
            path = args.output
        else:
            protocol = OptimizerDesignProtocol.load(args.validate)
            action = "validated"
            path = args.validate
        _print_or_write(
            {
                "action": action,
                "protocol_path": str(path.resolve()),
                "protocol_id": protocol.protocol_id,
                "protocol_digest": protocol.digest,
                "source_commit": SourceManifest.load(protocol.source_manifest_path).source_commit,
                "outer_wall_clock_seconds": protocol.outer_wall_clock_seconds,
                "candidate_timeout_seconds": protocol.candidate_timeout_seconds,
                "outer_seeds": list(protocol.outer_seeds),
                "held_out_evaluations_per_outer_run": len(protocol.held_out_seeds),
                "editable_paths": list(protocol.editable_paths),
                "formal_status": protocol.formal_status,
            }
        )
        return 0
    if args.command == "optimizer-design-baseline":
        protocol = OptimizerDesignProtocol.load(
            args.protocol,
            source_root=args.source_root,
            data_root=args.data_root,
            environment_python=args.environment_python,
        )
        gpu_ids = tuple(args.gpu_id)
        record = run_optimizer_design_baseline(
            protocol=protocol,
            source_root=args.source_root,
            data_root=args.data_root,
            environment_python=args.environment_python,
            gpu_ids=gpu_ids,
            cpu_set=args.cpu_set,
            memory_limit_gib=args.memory_limit_gib,
            output_dir=args.output_dir,
        )
        _print_or_write(
            {
                "status": record.status,
                "protocol_id": record.protocol_id,
                "completed_record_path": str(
                    (args.output_dir / "completed-baseline-score-record.json").resolve()
                ),
                "record_digest": record.digest,
                "final_score_steps": record.final_score_steps,
                "promotion_required": True,
            }
        )
        return 0
    if args.command == "autoresearch-baseline":
        protocol = AutoResearchProtocol.load(
            args.protocol,
            prepared_root=args.prepared_root,
            kernel_cache_root=args.kernel_cache_root,
        )
        baseline_manifest = BaselineManifest.load(protocol.baseline_manifest_path)
        _apply_autoresearch_resource_limits(args.cpu_set, args.memory_limit_gib)
        with _exclusive_gpu_lock(args.gpu_id):
            hardware = _formal_autoresearch_hardware(args.gpu_id)
            hardware.update(
                _formal_autoresearch_environment(protocol, args.environment_python)
            )
            evaluator = CandidateEvaluator(
                manifest=EvaluatorManifest.load(protocol.evaluator_manifest_path),
                prepared_root=args.prepared_root,
                command_prefix=(str(args.environment_python.expanduser().absolute()),),
                gpu_id=args.gpu_id,
                gpu_uuid=str(hardware["gpu_uuid"]),
                prepared_manifest=PreparedAssetManifest.load(
                    protocol.prepared_manifest_path
                ),
                kernel_cache_root=args.kernel_cache_root,
                kernel_cache_manifest=KernelCacheManifest.load(
                    protocol.kernel_cache_manifest_path
                ),
                sandbox=True,
                enforce_wall_clock_budget=True,
                attest_evaluate_bpb=True,
                protocol_digest=protocol.digest,
                benchmark_commit=baseline_manifest.source_commit,
            )
            record = run_autoresearch_baseline(
                protocol=protocol,
                prepared_root=args.prepared_root,
                evaluator=evaluator,
                hardware=hardware,
                output_dir=args.output_dir,
            )
        _print_or_write(
            {
                "status": record.status,
                "candidate": str(
                    (args.output_dir / "completed-baseline-score-record.json").resolve()
                ),
                "record_digest": record.digest,
                "final_score": record.final_score,
                "promotion_required": True,
            }
        )
        return 0
    if args.command == "baseline-verify":
        if args.benchmark == "autoresearch-architecture":
            from .AutoResearch.protocol import BaselineScoreRecord

            record = BaselineScoreRecord.load(args.candidate)
        else:
            from .OptimizerDesign.protocol import BaselineScoreRecord

            record = BaselineScoreRecord.load(args.candidate)
        if record.status != "completed":
            raise AdapterError("baseline verification requires a completed candidate")
        _print_or_write(
            {
                "benchmark_id": args.benchmark,
                "status": record.status,
                "record_digest": record.digest,
                "evidence_verified": True,
            }
        )
        return 0
    if args.command == "baseline-promote":
        _print_or_write(
            promote_baseline_record(
                benchmark_id=args.benchmark,
                candidate_path=args.candidate,
                destination_path=args.destination,
                expected_pending_sha256=args.expected_pending_sha256,
                acknowledge_reviewed=args.acknowledge_reviewed,
            )
        )
        return 0
    if args.command == "freeze-implementation-manifest":
        payload = freeze_implementation_manifest_candidate(
            source_manifest=args.source_manifest,
            output_path=args.output,
        )
        _print_or_write(
            {
                "action": "generated-for-review",
                "output": str(args.output.resolve()),
                "implementation_file_count": len(payload["implementation_files"]),
                "automatic_promotion": False,
            }
        )
        return 0
    if args.command == "optimizer-design-freeze-runtime":
        manifest = freeze_agent_runtime_manifest_candidate(
            source_manifest_path=args.source_manifest,
            output_path=args.output,
        )
        _print_or_write(
            {
                "action": "generated-for-review",
                "output": str(args.output.resolve()),
                "agent_count": len(manifest.agents),
                "manifest_digest": manifest.digest,
                "automatic_promotion": False,
            }
        )
        return 0
    if args.command == "terminal-ao-freeze-dataset":
        payload = freeze_terminal_dataset_candidate(
            dataset_dir=args.dataset_dir,
            source_split=args.source_split,
            output_path=args.output,
        )
        _print_or_write(
            {
                "action": "generated-for-review",
                "output": str(args.output.resolve()),
                "dataset_digest": payload["dataset_digest"],
                "membership_changed": payload["maintenance"]["membership_changed"],
                "automatic_promotion": False,
            }
        )
        return 0
    if args.command == "optimizer-design-aggregate":
        payload = aggregate_optimizer_design(
            protocol=OptimizerDesignProtocol.load(args.protocol),
            campaign_dir=args.campaign_dir,
            agent=args.agent,
        )
        _print_or_write(payload, args.output)
        return 0
    if args.command == "optimizer-design-scorecard":
        payload = optimizer_design_scorecard(
            protocol=OptimizerDesignProtocol.load(args.protocol),
            campaign_dir=args.campaign_dir,
        )
        _print_or_write(payload, args.output)
        return 0
    if args.command == "mle-cell":
        protocol = FormalProtocol.load(args.protocol)
        matching = [
            item
            for item in campaign_cells(protocol, args.campaign_dir, agents=(args.agent,))
            if item.task_id == args.competition_id and item.seed == args.seed
        ]
        if len(matching) != 1:
            raise AdapterError("requested MLE cell is outside the frozen protocol grid")
        outcome = run_campaign_cell(
            cell=matching[0],
            protocol=protocol,
            data_root=args.data_root,
            gpu_id=args.gpu_id,
            model_config=ModelTrackConfig.load(args.model_config, formal=True),
            agent_variant=args.agent_variant,
            gpu_ids=tuple(args.gpu_id),
        )
        result = outcome.result if hasattr(outcome, "result") else outcome
        _print_or_write(result.to_dict())
        return 0 if result.score_valid else 1
    if args.command == "formal-preflight":
        report = collect_formal_preflight(
            benchmark_id=args.benchmark,
            agent_id=args.agent,
            agent_variant=args.agent_variant,
            protocol_path=args.protocol,
            model_config_path=args.model_config,
            formal=not args.smoke,
            data_root=args.data_root,
        )
        _print_or_write(report.to_dict(), args.output)
        return 0 if report.passed else 1
    if args.command == "fml-aggregate":
        payload = aggregate_fml(
            protocol=FMLProtocol.load(args.protocol, formal=True),
            campaign_dir=args.campaign_dir,
            agent=args.agent,
        )
        _print_or_write(payload, args.output)
        return 0
    if args.command == "fml-scorecard":
        payload = fml_scorecard(
            protocol=FMLProtocol.load(args.protocol, formal=True),
            campaign_dir=args.campaign_dir,
        )
        _print_or_write(payload, args.output)
        return 0
    if args.command == "mle-aggregate":
        payload = aggregate_campaign(FormalProtocol.load(args.protocol), args.campaign_dir, args.agent)
        _print_or_write(payload, args.output)
        return 0
    if args.command == "mle-scorecard":
        payload = mle_scorecard(
            FormalProtocol.load(args.protocol), args.campaign_dir
        )
        _print_or_write(payload, args.output)
        return 0
    if args.command == "terminal-ao-aggregate":
        payload = aggregate_terminal_ao(
            protocol=TerminalAOProtocol.load(args.protocol),
            campaign_dir=args.campaign_dir,
            agent=args.agent,
        )
        _print_or_write(payload, args.output)
        return 0
    if args.command == "terminal-ao-scorecard":
        payload = terminal_ao_scorecard(
            protocol=TerminalAOProtocol.load(args.protocol),
            campaign_dir=args.campaign_dir,
        )
        _print_or_write(payload, args.output)
        return 0
    if args.command == "autoresearch-aggregate":
        payload = aggregate_autoresearch(
            protocol=AutoResearchProtocol.load(args.protocol),
            campaign_dir=args.campaign_dir,
            agent=args.agent,
        )
        _print_or_write(payload, args.output)
        if args.readiness_evidence is not None:
            if args.output is None or payload["formal_score_valid"] is not True:
                raise AdapterError(
                    "formal Autoresearch readiness evidence requires --output and a valid formal score"
                )
            write_json_exclusive(
                args.readiness_evidence,
                {
                    "agent": args.agent,
                    "mode": "autoresearch",
                    "protocol_digest": payload["protocol_digest"],
                    "evidence_kind": "formal_campaign",
                    "aggregate_path": str(args.output.resolve()),
                },
            )
        return 0
    if args.command == "autoresearch-scorecard":
        payload = autoresearch_scorecard(
            protocol=AutoResearchProtocol.load(args.protocol),
            campaign_dir=args.campaign_dir,
        )
        _print_or_write(payload, args.output)
        return 0
    if args.command == "scorecard":
        mle_protocol = FormalProtocol.load(args.mle_protocol)
        ao_protocol = TerminalAOProtocol.load(args.ao_protocol)
        payload = {
            "schema_version": 1,
            "comparison_policy": {
                "separate_benchmark_scorecards": True,
                "composite_score": None,
                "terminal_direct_89_excluded": True,
            },
            "readiness": collect_status(),
            "mle": {
                agent: aggregate_campaign(mle_protocol, args.mle_campaign_dir, agent)
                for agent in AGENTS
            },
            "terminal_ao": {
                agent: aggregate_terminal_ao(
                    protocol=ao_protocol,
                    campaign_dir=args.ao_campaign_dir,
                    agent=agent,
                )
                for agent in AGENTS
            },
        }
        _print_or_write(payload, args.output)
        return 0
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Shared baseline benchmark adapters")
    subparsers = parser.add_subparsers(dest="command", required=True)
    _mle_parser(subparsers)
    _autoresearch_parser(subparsers)
    _optimizer_design_parser(subparsers)
    _fml_parser(subparsers)
    _terminal_ao_parser(subparsers)
    _terminal_parser(
        subparsers,
        "terminal-direct-smoke",
        help_text="run a non-comparable direct Terminal-Bench infrastructure smoke",
    )
    _terminal_parser(
        subparsers,
        "terminal",
        help_text="deprecated alias for terminal-direct-smoke",
    )
    _formal_tools_parsers(subparsers)
    args = parser.parse_args(argv)
    handled = _handle_formal_tool(args)
    if handled is not None:
        return handled

    if args.command == "autoresearch":
        protocol = AutoResearchProtocol.load(
            args.protocol,
            prepared_root=None if args.dry_run else args.prepared_root,
            kernel_cache_root=None if args.dry_run else args.kernel_cache_root,
        )
        if args.seed not in protocol.outer_seeds:
            raise AdapterError("requested outer seed is outside the frozen Autoresearch protocol")
        run_kind = "smoke" if args.smoke else "pilot" if args.pilot else "formal"
        model_config = ModelTrackConfig.load(
            args.model_config,
            formal=run_kind == "formal",
        )
        if args.model is not None and args.model != model_config.outer_model_id:
            raise AdapterError("Autoresearch model override differs from model track")
        model_environment = _autoresearch_model_environment(
            model_config=model_config,
            proxy=args.proxy,
            include_credentials=not args.dry_run,
        )
        run_budget = (
            args.outer_budget_seconds
            if args.outer_budget_seconds is not None
            else 1800
            if args.smoke
            else 14400
            if args.pilot
            else protocol.outer_wall_clock_seconds
        )
        if run_kind == "formal" and run_budget != protocol.outer_wall_clock_seconds:
            raise AdapterError("formal Autoresearch runs must use the frozen 48-hour budget")
        if run_kind != "formal" and not 1 <= run_budget < protocol.outer_wall_clock_seconds:
            raise AdapterError("smoke/pilot Autoresearch runs require a reduced positive outer budget")
        request = NativeLaunchRequest(
            agent=args.agent,
            workspace=args.output_dir.resolve() / "launcher/workspace",
            output_dir=args.output_dir.resolve() / "launcher/native",
            socket_path=args.output_dir.resolve() / "launcher/capability/dev.sock",
            token="dry-run-capability-token",
            outer_seed=args.seed,
            timeout_seconds=run_budget,
            runtime_root=args.output_dir.resolve() / "launcher/runtime",
            model_environment=model_environment,
            native_step_limit=args.native_step_limit,
        )
        command = build_native_command(request)
        if args.dry_run:
            payload = {
                **_command_payload(command),
                "mode": "autoresearch",
                "dry_run": True,
                "non_comparable": True,
                "agent": args.agent,
                "native_component": AGENTS[args.agent].autoresearch_backend,
                "protocol_id": protocol.protocol_id,
                "protocol_digest": protocol.digest,
                "candidate_training_seconds": protocol.candidate_training_seconds,
                "candidate_hard_timeout_seconds": protocol.candidate_timeout_seconds,
                "outer_wall_clock_seconds": protocol.outer_wall_clock_seconds,
                "outer_seeds": list(protocol.outer_seeds),
                "held_out_evaluations_per_outer_run": 2,
                "editable_paths": list(protocol.editable_paths),
                "final_artifact": str(args.output_dir.resolve() / "artifacts/final/train.py"),
                "score_source": "two sealed held-out evaluations after dev-best replay",
                "model_provider_wiring": "explicit-shared-model-track-adapter",
                "model_identity": model_identity(
                    model_config.outer_model_id, model_config.relay_base_url
                ),
                "model_track_id": model_config.model_track_id,
                "formal_baseline_ready": protocol.formal_baseline_ready,
                "cpu_set": args.cpu_set,
                "memory_limit_gib": args.memory_limit_gib,
                "run_kind": run_kind,
                "outer_budget_seconds": run_budget,
            }
            print(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True))
            return 0
        if run_kind == "formal":
            protocol.require_formal_baseline()
        environment_python = (
            args.environment_python or protocol.source_root / ".venv/bin/python"
        ).expanduser().absolute()
        if args.kernel_cache_root is None:
            raise AdapterError("formal Autoresearch runs require --kernel-cache-root")
        environment_record = _formal_autoresearch_environment(protocol, environment_python)
        _apply_autoresearch_resource_limits(args.cpu_set, args.memory_limit_gib)
        with _exclusive_gpu_lock(args.gpu_id):
            hardware = _formal_autoresearch_hardware(args.gpu_id)
            hardware.update(environment_record)
            evaluator = CandidateEvaluator(
                manifest=EvaluatorManifest.load(protocol.evaluator_manifest_path),
                prepared_root=args.prepared_root,
                command_prefix=(str(environment_python),),
                gpu_id=args.gpu_id,
                gpu_uuid=str(hardware["gpu_uuid"]),
                prepared_manifest=PreparedAssetManifest.load(protocol.prepared_manifest_path),
                kernel_cache_root=args.kernel_cache_root,
                kernel_cache_manifest=KernelCacheManifest.load(protocol.kernel_cache_manifest_path),
                sandbox=True,
                enforce_wall_clock_budget=True,
                attest_evaluate_bpb=True,
                protocol_digest=protocol.digest,
                benchmark_commit=BaselineManifest.load(
                    protocol.baseline_manifest_path
                ).source_commit,
            )
            result = run_autoresearch(
                agent=args.agent,
                protocol=protocol,
                prepared_root=args.prepared_root,
                output_dir=args.output_dir,
                outer_seed=args.seed,
                search_runner=NativeCommandSearchRunner(
                    sandbox=True,
                    model_environment=model_environment,
                    native_step_limit=args.native_step_limit,
                ),
                evaluator=evaluator,
                formal=run_kind == "formal",
                model_identity=model_identity(
                    model_config.outer_model_id, model_config.relay_base_url
                ),
                hardware=hardware,
                outer_wall_clock_seconds=run_budget,
                run_kind=run_kind,
                model_config=model_config,
                agent_variant=args.agent_variant,
            )
        _print_or_write(result.to_dict())
        if args.readiness_evidence is not None:
            if run_kind != "smoke" or not result.score_valid:
                raise AdapterError(
                    "Autoresearch readiness evidence can only publish a successful real smoke"
                )
            write_json_exclusive(
                args.readiness_evidence,
                {
                    "agent": args.agent,
                    "mode": "autoresearch",
                    "protocol_digest": protocol.digest,
                    "evidence_kind": "real_smoke",
                    "non_comparable": True,
                    "result_path": str((args.output_dir / "result.json").resolve()),
                },
            )
        return 0 if result.score_valid else 1
    if args.command == "optimizer-design":
        protocol = OptimizerDesignProtocol.load(args.protocol)
        run_kind = "smoke" if args.smoke else "pilot" if args.pilot else "formal"
        model_config = ModelTrackConfig.load(
            args.model_config,
            formal=run_kind == "formal",
        )
        if args.model is not None and args.model != model_config.outer_model_id:
            raise AdapterError("Optimizer Design model override differs from model track")
        run_budget = (
            args.outer_budget_seconds
            if args.outer_budget_seconds is not None
            else 1800
            if args.smoke
            else 14400
            if args.pilot
            else protocol.outer_wall_clock_seconds
        )
        gpu_ids = tuple(args.gpu_id)
        if run_kind == "formal" and not args.dry_run and len(gpu_ids) != 4:
            raise AdapterError(
                "formal Optimizer Design requires exactly four explicit --gpu-id values"
            )
        if run_kind == "formal" and not args.dry_run:
            protocol.require_formal_ready()
        model_environment = _autoresearch_model_environment(
            model_config=model_config,
            proxy=args.proxy,
            include_credentials=not args.dry_run,
            task_name="Optimizer Design",
        )
        request = OptimizerDesignRequest(
            agent=args.agent,
            protocol_path=args.protocol,
            output_dir=args.output_dir,
            outer_seed=args.seed,
            source_root=args.source_root,
            data_root=args.data_root,
            environment_python=args.environment_python,
            gpu_ids=gpu_ids,
            cpu_set=args.cpu_set,
            memory_limit_gib=args.memory_limit_gib,
            outer_budget_seconds=run_budget,
            run_kind=run_kind,
            native_step_limit=args.native_step_limit,
            model_environment=model_environment,
            model_identity=model_identity(
                model_config.outer_model_id, model_config.relay_base_url
            ),
            dry_run=args.dry_run,
            model_config=model_config,
            agent_variant=args.agent_variant,
        )
        adapter = get_agent_adapter(args.agent).optimizer_design
        if args.dry_run:
            command = adapter.build_command(request)
            payload = {
                **_command_payload(command),
                "mode": "optimizer-design",
                "agent": args.agent,
                "native_component": AGENTS[args.agent].optimizer_design_backend,
                "protocol_id": protocol.protocol_id,
                "protocol_digest": protocol.digest,
                "source_commit": SourceManifest.load(protocol.source_manifest_path).source_commit,
                "editable_paths": list(protocol.editable_paths),
                "outer_seeds": list(protocol.outer_seeds),
                "held_out_evaluations_per_outer_run": len(protocol.held_out_seeds),
                "gpu_ids": list(gpu_ids),
                "cpu_set": args.cpu_set,
                "memory_limit_gib": args.memory_limit_gib,
                "run_kind": run_kind,
                "outer_budget_seconds": run_budget,
                "formal_status": protocol.formal_status,
                "non_comparable_to_arbor_4xa100": True,
            }
            _print_or_write(payload)
            return 0
        result = adapter.run(request)
        _print_or_write(result.to_dict())
        return 0 if result.score_valid else 1
    if args.command == "fml-review-protocol":
        protocol = build_review_candidate(upstream_root=args.upstream_root)
        protocol.write(args.output)
        _print_or_write(
            {
                "action": "generated-for-review",
                "protocol_path": str(args.output.resolve()),
                "protocol_digest": protocol.digest,
                "automatic_promotion": False,
                "audit": audit_report(protocol),
            }
        )
        return 0
    if args.command == "fml-readiness":
        _print_or_write(
            collect_fml_readiness(
                protocol_path=args.protocol,
                formal_evidence_root=args.formal_evidence_root,
            ),
            args.output,
        )
        return 0
    if args.command == "fml":
        formal = not args.smoke and not args.dry_run
        protocol = FMLProtocol.load(args.protocol, formal=formal)
        model_config = ModelTrackConfig.load(args.model_config, formal=formal)
        request = FMLRunRequest(
            agent=args.agent,
            protocol=protocol,
            model_config=model_config,
            task_config=args.task_config,
            output_dir=args.output_dir,
            outer_run_index=args.outer_run_index,
            agent_variant=args.agent_variant,
            gpu_ids=tuple(args.gpu_id),
            formal=formal,
            credential_env_names=tuple(args.credential_env),
        )
        adapter = FMLBenchmarkAdapter(args.agent)
        if args.dry_run:
            command = adapter.build_command(request)
            payload = {
                **_command_payload(command),
                "benchmark_id": "fml-bench",
                "outer_run_index": args.outer_run_index,
                "task_id": args.task_config.stem,
                "dry_run": True,
                "non_formal": True,
                "non_comparable": True,
                "credential_env_count": len(args.credential_env),
            }
            _print_or_write(payload)
            return 0
        record = run_fml_task(request)
        _print_or_write(
            {
                "benchmark_id": "fml-bench",
                "task_id": record.task_id,
                "outer_run_index": record.outer_run_index,
                "status": record.status,
                "score_valid": record.score_valid,
                "task_record_digest": record.digest,
                "non_formal": not formal,
                "non_comparable": not formal,
            }
        )
        return 0 if record.score_valid else 1
    if args.command == "mle":
        if args.model is None or not args.upstream_base_url:
            raise AdapterError(
                "direct MLE smoke requires explicit --model and --upstream-base-url; "
                "formal MLE uses mle-cell --model-config"
            )
        request = MleLiteRequest(
            agent=args.agent,
            competition_id=args.competition_id,
            data_root=args.data_root,
            output_dir=args.output_dir,
            gpu_id=args.gpu_id,
            steps=args.steps,
            timeout_seconds=args.timeout,
            model=args.model,
            upstream_base_url=args.upstream_base_url,
            proxy=args.proxy,
            run_tag=args.run_tag,
            instruction=args.instruction,
            max_turns=args.max_turns,
            config_path=args.config_path,
            force=args.force,
            dry_run=args.dry_run,
            seed=args.seed,
        )
        adapter = get_agent_adapter(args.agent).mle_lite
        if args.dry_run:
            command = adapter.build_command(request)
            print(json.dumps(_command_payload(command), indent=2, ensure_ascii=False))
            return 0
        submission = adapter.run(request)
        print(submission)
        return 0
    elif args.command == "terminal-ao":
        model_config = ModelTrackConfig.load(
            args.model_config,
            formal=not args.dry_run,
            require_terminal_inner=True,
        )
        request = TerminalAORequest(
            agent=args.agent,
            protocol_path=args.protocol,
            output_dir=args.output_dir,
            model=model_config.outer_model_id,
            upstream_base_url=model_config.relay_base_url,
            proxy=args.proxy,
            seed=args.seed,
            timeout_seconds=args.timeout,
            dry_run=args.dry_run,
            model_config_path=args.model_config,
            agent_variant=args.agent_variant,
            gpu_ids=tuple(args.gpu_id),
        )
        command = get_agent_adapter(args.agent).build_terminal_ao_command(request)
    else:
        if args.model is None or not args.upstream_base_url:
            raise AdapterError(
                "Terminal direct smoke requires explicit --model and --upstream-base-url"
            )
        request = HarborTerminalRequest(
            agent=args.agent,
            dataset_dir=args.dataset_dir,
            jobs_dir=args.jobs_dir,
            model=args.model,
            upstream_base_url=args.upstream_base_url,
            proxy=args.proxy,
            attempts=args.attempts,
            concurrency=args.concurrency,
            agent_concurrency=args.agent_concurrency,
            task_names=tuple(args.task),
            exclude_task_names=tuple(args.exclude_task),
            agent_kwargs=tuple(args.agent_kwarg),
            job_name=args.job_name,
            max_retries=args.max_retries,
            timeout_multiplier=args.timeout_multiplier,
            force_build=args.force_build,
            dry_run=args.dry_run,
        )
        command = get_agent_adapter(args.agent).build_terminal_command(request)

    if args.dry_run:
        payload = _command_payload(command)
        if args.command in {"terminal", "terminal-direct-smoke"}:
            payload.update(
                {
                    "mode": "terminal-direct-smoke",
                    "non_comparable_to_terminal_ao": True,
                    "deprecated_alias": args.command == "terminal",
                }
            )
        elif args.command == "terminal-ao":
            payload["mode"] = "terminal-ao"
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    result = run_command(command)
    print(result.stdout, end="")
    return result.return_code


def cli_entrypoint(argv: list[str] | None = None) -> int:
    try:
        return main(argv)
    except AdapterError as exc:
        _print_or_write(
            {
                "status": "failed",
                "score_valid": False,
                "error_type": type(exc).__name__,
                "failure_reason": str(exc),
            }
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(cli_entrypoint())
