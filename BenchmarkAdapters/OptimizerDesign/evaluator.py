"""Host-owned policy validation and evaluation for Optimizer Design candidates."""

from __future__ import annotations

import ast
import hashlib
import json
import math
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from ..contracts import AdapterError
from ..protocol import canonical_json, sha256_file, write_json_exclusive
from .protocol import OptimizerDesignProtocol, SourceManifest


MAX_CANDIDATE_BYTES = 2 * 1024 * 1024
MAX_TRAIN_STEPS = 3800
FROZEN_MODEL_SIGNATURE_SHA256 = "e04531892a9b2737babed300df0a7d4eaaea97012491f75a56a208854baadf2c"
_VAL_LINE = r"step:(\d+)/(\d+)\s+val_loss:([^\s]+)"
_SECTION_TITLES = (
    "#              Optimizer",
    "#                Setup",
    "#       Init & Optim Hyperparams",
    "#        Training and Validation",
)


def _section_offset(source: str, title: str) -> int:
    positions = [match.start() for match in re.finditer(re.escape(title), source)]
    if len(positions) != 1:
        raise AdapterError(f"Optimizer Design candidate must contain exactly one section: {title}")
    line_start = source.rfind("\n", 0, positions[0]) + 1
    separator_start = source.rfind("########################################", 0, line_start)
    return separator_start if separator_start >= 0 else line_start


def _frozen_chunks(source: str) -> tuple[str, str, str]:
    optimizer, setup, initialization, training = (
        _section_offset(source, title) for title in _SECTION_TITLES
    )
    if not 0 < optimizer < setup < initialization < training:
        raise AdapterError("Optimizer Design candidate section order differs from the baseline")
    return source[:optimizer], source[setup:initialization], source[training:]


def _train_steps(tree: ast.AST) -> int:
    values: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "train_steps" for target in node.targets):
            continue
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, int):
            values.append(node.value.value)
    if len(values) != 1 or not 1 <= values[0] <= MAX_TRAIN_STEPS:
        raise AdapterError(
            f"Optimizer Design candidate requires one literal train_steps in [1, {MAX_TRAIN_STEPS}]"
        )
    return values[0]


def _root_name(node: ast.AST) -> str | None:
    while isinstance(node, (ast.Attribute, ast.Subscript)):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def _protected_global_names() -> set[str]:
    return {
        "AdamW",
        "Block",
        "RuntimeError",
        "CausalSelfAttention",
        "F",
        "GPT",
        "Linear",
        "MLP",
        "Path",
        "RMSNorm",
        "Rotary",
        "Tensor",
        "_load_data_shard",
        "batch_size",
        "bool",
        "code",
        "device",
        "dist",
        "distributed_data_generator",
        "logfile",
        "mbs",
        "model",
        "nn",
        "num_trials",
        "os",
        "print0",
        "repr",
        "str",
        "sys",
        "torch",
        "tuple",
        "train_loader",
        "val_inputs",
        "val_loss",
        "val_targets",
        "val_tokens",
        "type",
    }


def _outer_expression(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> ast.AST:
    current = node
    while True:
        parent = parents.get(current)
        if isinstance(parent, (ast.Attribute, ast.Subscript)) and parent.value is current:
            current = parent
            continue
        if isinstance(parent, ast.Call) and parent.func is current:
            current = parent
            continue
        return current


def validate_candidate(source: str, baseline_source: str) -> int:
    encoded = source.encode("utf-8")
    if not encoded or len(encoded) > MAX_CANDIDATE_BYTES:
        raise AdapterError("Optimizer Design candidate is empty or exceeds the size limit")
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise AdapterError("Optimizer Design candidate is not valid Python") from exc
    if _frozen_chunks(source) != _frozen_chunks(baseline_source):
        raise AdapterError(
            "Optimizer Design candidate changed frozen data, architecture, setup, or training-loop code"
        )
    allowed_imports = {
        "collections",
        "dataclasses",
        "functools",
        "itertools",
        "math",
        "os",
        "pathlib",
        "sys",
        "time",
        "torch",
        "typing",
        "uuid",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name.split(".", 1)[0] for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [(node.module or "").split(".", 1)[0]]
            if (node.module or "").startswith(("torch.autograd", "torch.func")):
                raise AdapterError("Optimizer Design candidate imports an extra differentiation API")
        else:
            continue
        if any(name not in allowed_imports for name in names):
            raise AdapterError("Optimizer Design candidate imports a third-party optimizer library")
    optimizer_offset = _section_offset(source, _SECTION_TITLES[0])
    setup_offset = _section_offset(source, _SECTION_TITLES[1])
    initialization_offset = _section_offset(source, _SECTION_TITLES[2])
    training_offset = _section_offset(source, _SECTION_TITLES[3])
    optimizer_start = source[:optimizer_offset].count("\n") + 1
    setup_start = source[:setup_offset].count("\n") + 1
    initialization_start = source[:initialization_offset].count("\n") + 1
    training_start = source[:training_offset].count("\n") + 1
    mutable_lines = set(range(optimizer_start, setup_start)) | set(
        range(initialization_start, training_start)
    )
    protected_globals = _protected_global_names()
    protected_roots = protected_globals | {"time", "uuid"}
    forbidden_references = {
        "_load_data_shard",
        "__builtins__",
        "breakpoint",
        "code",
        "compile",
        "dir",
        "eval",
        "exec",
        "getattr",
        "globals",
        "help",
        "input",
        "locals",
        "logfile",
        "open",
        "os",
        "print",
        "print0",
        "setattr",
        "sys",
        "vars",
    }
    forbidden_attributes = {
        "__builtins__",
        "__closure__",
        "__code__",
        "__dict__",
        "__globals__",
        "__loader__",
        "__mro__",
        "__spec__",
        "__subclasses__",
        "backward",
        "chmod",
        "connect",
        "fork",
        "from_file",
        "get_rng_state",
        "hardlink_to",
        "listen",
        "load",
        "open",
        "initial_seed",
        "manual_seed",
        "manual_seed_all",
        "popen",
        "read_bytes",
        "read_text",
        "recv",
        "rename",
        "replace",
        "save",
        "send",
        "seed",
        "set_rng_state",
        "spawnl",
        "spawnle",
        "spawnlp",
        "spawnlpe",
        "spawnv",
        "spawnve",
        "spawnvp",
        "spawnvpe",
        "symlink_to",
        "system",
        "unlink",
        "write_bytes",
        "write_text",
    }
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    for node in ast.walk(tree):
        if not hasattr(node, "lineno") or node.lineno not in mutable_lines:
            continue
        if (
            isinstance(node, ast.Name)
            and isinstance(node.ctx, ast.Store)
            and node.id in protected_globals
        ):
            raise AdapterError(
                f"Optimizer Design candidate assigns frozen benchmark name: {node.id}"
            )
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name in protected_globals:
                raise AdapterError(
                    f"Optimizer Design candidate redefines frozen benchmark name: {node.name}"
                )
        if isinstance(node, ast.arg) and node.arg in protected_globals:
            raise AdapterError(
                f"Optimizer Design candidate shadows frozen benchmark name: {node.arg}"
            )
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            aliases = [alias.asname or alias.name.rsplit(".", 1)[-1] for alias in node.names]
            if any(alias in protected_globals for alias in aliases):
                raise AdapterError("Optimizer Design candidate shadows a frozen benchmark import")
        if isinstance(node, ast.Name) and node.id in forbidden_references:
            raise AdapterError(
                f"Optimizer Design candidate accesses a host-controlled name: {node.id}"
            )
        if isinstance(node, ast.Name) and node.id in {
            "distributed_data_generator",
            "train_loader",
            "val_inputs",
            "val_targets",
            "val_tokens",
        }:
            raise AdapterError(
                f"Optimizer Design candidate accesses protected benchmark data: {node.id}"
            )
        if isinstance(node, ast.Name) and node.id == "model" and isinstance(node.ctx, ast.Load):
            expression = ast.unparse(_outer_expression(node, parents))
            if expression not in {
                "model.blocks.parameters()",
                "model.embed.weight",
                "model.named_parameters()",
                "model.parameters()",
                "model.proj.weight",
            }:
                raise AdapterError("Optimizer Design candidate accesses the protected model object")
        if isinstance(node, ast.Attribute) and node.attr in forbidden_attributes:
            raise AdapterError(
                f"Optimizer Design candidate uses a forbidden host-access attribute: {node.attr}"
            )
        if isinstance(node, ast.Attribute):
            attribute_name = ast.unparse(node)
            if attribute_name.startswith(("torch.autograd", "torch.func")):
                raise AdapterError("Optimizer Design candidate accesses an extra differentiation API")
        if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            if node.attr != "__init__":
                raise AdapterError("Optimizer Design candidate uses forbidden Python introspection")
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in {"eval", "exec", "compile", "__import__"}
        ):
            raise AdapterError("Optimizer Design candidate uses forbidden dynamic execution")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            root = _root_name(node.func)
            call_name = ast.unparse(node.func)
            if call_name.startswith(("torch.autograd.", "torch.func.")):
                raise AdapterError("Optimizer Design candidate performs extra differentiation")
            if root == "model" and node.func.attr not in {"named_parameters", "parameters"}:
                raise AdapterError("Optimizer Design candidate invokes a forbidden model operation")
            if root in protected_roots - {"model", "torch", "dist", "F", "nn"}:
                raise AdapterError(
                    f"Optimizer Design candidate invokes a protected benchmark object: {root}"
                )
        if isinstance(node, (ast.Attribute, ast.Subscript)) and isinstance(node.ctx, ast.Store):
            if _root_name(node) in protected_roots | {"dist", "torch"}:
                raise AdapterError("Optimizer Design candidate mutates protected benchmark objects")
    backward_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "backward"
    ]
    if len(backward_calls) != 1:
        raise AdapterError("Optimizer Design candidate must retain one forward-backward call per step")
    return _train_steps(tree)


def inject_seed(source: str, seed: int, *, score_marker: str | None = None) -> str:
    marker = "for _ in range(num_trials):"
    if source.count(marker) != 1:
        raise AdapterError("Optimizer Design candidate seed injection marker differs from the baseline")
    injection = (
        f"torch.manual_seed({seed})\n"
        f"torch.cuda.manual_seed_all({seed})\n\n"
        f"{marker}"
    )
    transformed = source.replace(marker, injection, 1)
    if score_marker is not None:
        hashlib_name = f"_optimizer_design_hashlib_{score_marker.lower()}"
        builtins_name = f"_optimizer_design_builtins_{score_marker.lower()}"
        guard_name = f"_optimizer_design_guard_{score_marker.lower()}"
        setup_marker = "model.compile(dynamic=False)"
        if transformed.count(setup_marker) != 1:
            raise AdapterError("Optimizer Design model attestation marker differs from baseline")
        capture = (
            f"{setup_marker}\n"
            f"import hashlib as {hashlib_name}\n"
            f"import builtins as {builtins_name}\n"
            f"{guard_name} = (\n"
            "    torch.Tensor.backward, torch.autograd.backward, torch.autograd.grad,\n"
            "    F.cross_entropy, dist.all_reduce, dist.broadcast,\n"
            "    distributed_data_generator, _load_data_shard,\n"
            "    GPT.forward, Block.forward, CausalSelfAttention.forward,\n"
            "    MLP.forward, RMSNorm.forward, Linear.forward, Rotary.forward,\n"
            ")"
        )
        transformed = transformed.replace(setup_marker, capture, 1)
        training_marker = 'train_loader = distributed_data_generator("data/fineweb10B/fineweb_train_*.bin", batch_size)'
        if transformed.count(training_marker) != 1:
            raise AdapterError("Optimizer Design training attestation marker differs from baseline")
        attestation = (
            f"if {guard_name} != (\n"
            "        torch.Tensor.backward, torch.autograd.backward, torch.autograd.grad,\n"
            "        F.cross_entropy, dist.all_reduce, dist.broadcast,\n"
            "        distributed_data_generator, _load_data_shard,\n"
            "        GPT.forward, Block.forward, CausalSelfAttention.forward,\n"
            "        MLP.forward, RMSNorm.forward, Linear.forward, Rotary.forward,\n"
            "    ):\n"
            f"        raise {builtins_name}.RuntimeError('Optimizer Design protected callable changed')\n"
            f"    if {hashlib_name}.sha256({builtins_name}.repr((\n"
            f"        {builtins_name}.tuple((name, {builtins_name}.tuple(parameter.shape), "
            f"{builtins_name}.str(parameter.dtype), {builtins_name}.bool(parameter.requires_grad)) "
            "for name, parameter in model.named_parameters()),\n"
            f"        {builtins_name}.tuple((name, {builtins_name}.type(module).__module__, "
            f"{builtins_name}.type(module).__qualname__) "
            "for name, module in model.named_modules()),\n"
            f"    )).encode()).hexdigest() != '{FROZEN_MODEL_SIGNATURE_SHA256}':\n"
            f"        raise {builtins_name}.RuntimeError('Optimizer Design model structure changed')\n"
            f"    {training_marker}"
        )
        transformed = transformed.replace(training_marker, attestation, 1)
        step_guard_marker = "    for step in range(train_steps + 1):"
        if transformed.count(step_guard_marker) != 1:
            raise AdapterError("Optimizer Design per-step guard marker differs from baseline")
        step_lines = attestation.removesuffix(f"\n    {training_marker}").splitlines()
        step_guard = "\n".join(
            ["        " + step_lines[0]]
            + ["    " + line if line else line for line in step_lines[1:]]
        )
        transformed = transformed.replace(
            step_guard_marker,
            f"{step_guard_marker}\n\n{step_guard}",
            1,
        )
        validation_marker = 'print0(f"step:{step}/{train_steps} val_loss:'
        if transformed.count(validation_marker) != 1:
            raise AdapterError("Optimizer Design validation instrumentation marker differs from baseline")
        transformed = transformed.replace(
            validation_marker,
            f'print0(f"{score_marker} step:{{step}}/{{train_steps}} val_loss:',
            1,
        )
    return transformed


def validation_trajectory(
    stdout: str,
    *,
    score_marker: str | None = None,
) -> tuple[tuple[tuple[int, float], ...], int]:
    records: list[tuple[int, float]] = []
    declared_steps: set[int] = set()
    prefix = rf"{re.escape(score_marker)}\s+" if score_marker is not None else ""
    for match in re.finditer(prefix + _VAL_LINE, stdout):
        step = int(match.group(1))
        declared_steps.add(int(match.group(2)))
        try:
            loss = float(match.group(3))
        except ValueError as exc:
            raise AdapterError("Optimizer Design emitted a non-numeric validation loss") from exc
        if not math.isfinite(loss) or loss <= 0:
            raise AdapterError("Optimizer Design emitted an invalid validation loss")
        records.append((step, loss))
    if not records or len(declared_steps) != 1:
        raise AdapterError("Optimizer Design output contains no unambiguous validation trajectory")
    if len({step for step, _loss in records}) != len(records):
        raise AdapterError("Optimizer Design output contains duplicate validation steps")
    steps = [step for step, _loss in records]
    if steps[0] != 0 or steps != sorted(steps):
        raise AdapterError("Optimizer Design validation steps are not a monotonic trajectory from zero")
    return tuple(records), next(iter(declared_steps))


def parse_validation(
    stdout: str,
    *,
    threshold: float,
    penalty: int,
    score_marker: str | None = None,
) -> tuple[int, float | None, int]:
    records, declared_steps = validation_trajectory(stdout, score_marker=score_marker)
    qualifying = [(step, loss) for step, loss in records if loss <= threshold]
    if not qualifying:
        return penalty, records[-1][1], declared_steps
    step, loss = min(qualifying, key=lambda item: item[0])
    return step, loss, declared_steps


def score_validation_trajectories(
    trajectories: tuple[tuple[tuple[int, float], ...], ...],
    *,
    target: float,
    significance_margin: float,
    penalty: int,
) -> tuple[int, float | None]:
    if not trajectories:
        raise AdapterError("Optimizer Design final scoring requires held-out trajectories")
    mapped = [dict(trajectory) for trajectory in trajectories]
    if any(not item for item in mapped):
        raise AdapterError("Optimizer Design final scoring received an empty trajectory")
    common_steps = sorted(set.intersection(*(set(item) for item in mapped)))
    for step in common_steps:
        mean_loss = sum(item[step] for item in mapped) / len(mapped)
        if (target - mean_loss) * len(mapped) ** 0.5 >= significance_margin:
            return step, mean_loss
    return penalty, None


@dataclass(frozen=True)
class OptimizerDesignEvaluation:
    evaluation_id: str
    status: str
    score_valid: bool
    score_steps: int | None
    target_reached: bool
    val_loss: float | None
    train_steps: int | None
    seed: int
    candidate_sha256: str
    stdout_sha256: str
    wall_clock_seconds: float
    failure_reason: str | None
    validation_trajectory: tuple[tuple[int, float], ...] = ()
    schema_version: int = 2
    protocol_digest: str | None = None
    benchmark_commit: str | None = None
    evaluator_digest: str | None = None
    environment_digest: str | None = None
    gpu_ids: tuple[str, ...] = ()

    @property
    def digest(self) -> str:
        return hashlib.sha256(canonical_json(asdict(self))).hexdigest()


class OptimizerDesignEvaluator:
    def __init__(
        self,
        *,
        protocol: OptimizerDesignProtocol,
        source_root: Path,
        data_root: Path,
        environment_python: Path,
        gpu_ids: tuple[str, ...] = ("0", "1", "2", "3"),
        timeout_seconds: int = 7200,
        execute: bool = True,
        sandbox: bool = True,
        validate_assets: bool = True,
    ) -> None:
        self.protocol = protocol
        self.source_root = source_root.resolve()
        self.data_root = data_root.resolve()
        self.environment_python = environment_python.expanduser().absolute()
        self.gpu_ids = gpu_ids
        self.timeout_seconds = timeout_seconds
        self.execute = execute
        self.sandbox = sandbox
        if len(self.gpu_ids) != protocol.gpu_count or len(set(self.gpu_ids)) != len(self.gpu_ids):
            raise AdapterError("Optimizer Design evaluator requires four unique GPU IDs")
        if validate_assets:
            protocol.validate(
                self.source_root if execute else None,
                self.data_root if execute else None,
                self.environment_python if execute else None,
            )
        else:
            protocol.validate()
        if execute and sandbox and shutil.which("bwrap") is None:
            raise AdapterError("Optimizer Design evaluator requires bubblewrap isolation")
        source_manifest = SourceManifest.load(protocol.source_manifest_path)
        self.source_manifest = source_manifest
        self.baseline_source = (self.source_root / source_manifest.editable_path).read_text(
            encoding="utf-8"
        )

    def _record_binding(self) -> dict[str, object]:
        return {
            "schema_version": 2,
            "protocol_digest": self.protocol.digest,
            "benchmark_commit": self.source_manifest.source_commit,
            "evaluator_digest": self.protocol.evaluator_manifest_digest,
            "environment_digest": self.protocol.environment_manifest_digest,
            "gpu_ids": self.gpu_ids,
        }

    @staticmethod
    def _parent_dirs(argv: list[str], target: Path, created: set[Path]) -> None:
        current = Path("/")
        for part in target.parent.parts[1:]:
            current /= part
            if current in created or current in {
                Path("/bin"),
                Path("/lib"),
                Path("/lib64"),
                Path("/usr"),
            }:
                continue
            argv.extend(("--dir", str(current)))
            created.add(current)

    @classmethod
    def _bind(
        cls,
        argv: list[str],
        source: Path,
        target: Path,
        created: set[Path],
        *,
        read_only: bool,
    ) -> None:
        source = source.resolve()
        if not source.exists():
            raise AdapterError(f"Optimizer Design sandbox mount is missing: {source}")
        cls._parent_dirs(argv, target, created)
        argv.extend(("--ro-bind" if read_only else "--bind", str(source), str(target)))

    def _sandbox_command(self, command: tuple[str, ...], workspace: Path, script: Path) -> tuple[str, ...]:
        bubblewrap = Path(shutil.which("bwrap") or "")
        if not bubblewrap.is_file():
            raise AdapterError("Optimizer Design evaluator requires bubblewrap isolation")
        argv = [
            str(bubblewrap),
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
            "/sys",
            "/sys",
            "--proc",
            "/proc",
            "--dev-bind",
            "/dev",
            "/dev",
            "--tmpfs",
            "/dev/shm",
            "--tmpfs",
            "/tmp",
            "--dir",
            "/tmp/home",
            "--ro-bind",
            "/etc",
            "/etc",
        ]
        created = {Path("/tmp"), Path("/tmp/home")}
        self._bind(argv, workspace, workspace, created, read_only=False)
        self._bind(argv, script, script, created, read_only=True)
        self._bind(
            argv,
            self.data_root / "fineweb10B",
            workspace / "data/fineweb10B",
            created,
            read_only=True,
        )
        requested_runtime = self.environment_python.parent.parent.absolute()
        interpreter_target = self.environment_python.readlink()
        alias_runtime = (
            interpreter_target.parents[1]
            if interpreter_target.is_absolute()
            else (self.environment_python.parent / interpreter_target).absolute().parents[1]
        )
        resolved_runtime = self.environment_python.resolve().parents[1]
        self._bind(argv, requested_runtime, requested_runtime, created, read_only=True)
        if alias_runtime != requested_runtime.resolve():
            self._bind(argv, alias_runtime, alias_runtime, created, read_only=True)
        if resolved_runtime != requested_runtime.resolve():
            self._bind(argv, resolved_runtime, resolved_runtime, created, read_only=True)
        argv.extend(("--chdir", str(workspace), "--", *command))
        return tuple(argv)

    def _execute(
        self,
        command: tuple[str, ...],
        workspace: Path,
        environment: dict[str, str],
        timeout_seconds: float,
    ) -> str:
        try:
            process = subprocess.Popen(
                command,
                cwd=workspace,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=True,
            )
        except OSError as exc:
            raise AdapterError(f"could not launch Optimizer Design candidate: {exc}") from exc
        try:
            stdout, _ = process.communicate(timeout=timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            os.killpg(process.pid, signal.SIGKILL)
            remaining, _ = process.communicate()
            stdout = (exc.stdout or "") + remaining
            raise AdapterError(
                f"Optimizer Design candidate exceeded {timeout_seconds:.3f}s hard timeout"
            ) from exc
        if process.returncode:
            raise AdapterError(f"Optimizer Design candidate exited with code {process.returncode}")
        return stdout

    def evaluate(
        self,
        candidate_path: Path,
        *,
        seed: int,
        output_dir: Path,
        evaluation_id: str,
        timeout_seconds: float | None = None,
    ) -> OptimizerDesignEvaluation:
        candidate_path = candidate_path.resolve()
        if not candidate_path.is_file() or candidate_path.is_symlink():
            raise AdapterError("Optimizer Design candidate must be a regular file")
        source = candidate_path.read_text(encoding="utf-8")
        candidate_sha256 = sha256_file(candidate_path)
        started = time.monotonic()
        output_dir = output_dir.resolve()
        output_dir.mkdir(parents=True, exist_ok=False)
        effective_timeout = self.timeout_seconds if timeout_seconds is None else timeout_seconds
        if effective_timeout <= 0 or effective_timeout > self.timeout_seconds:
            raise AdapterError("Optimizer Design evaluation timeout is outside the allowed bound")
        stdout = ""
        try:
            train_steps = validate_candidate(source, self.baseline_source)
            if not self.execute:
                result = OptimizerDesignEvaluation(
                    evaluation_id=evaluation_id,
                    status="policy_validated",
                    score_valid=False,
                    score_steps=None,
                    target_reached=False,
                    val_loss=None,
                    train_steps=train_steps,
                    seed=seed,
                    candidate_sha256=candidate_sha256,
                    stdout_sha256=hashlib.sha256(b"").hexdigest(),
                    wall_clock_seconds=time.monotonic() - started,
                    failure_reason="execution disabled",
                    validation_trajectory=(),
                    **self._record_binding(),
                )
            else:
                with tempfile.TemporaryDirectory(prefix="optimizer-design-") as temporary_name:
                    workspace = Path(temporary_name)
                    script = workspace / "records/track_3_optimization/train_gpt_simple.py"
                    script.parent.mkdir(parents=True)
                    score_marker = f"OPTIMIZER_DESIGN_SCORE_{secrets.token_hex(24)}"
                    script.write_text(
                        inject_seed(source, seed, score_marker=score_marker),
                        encoding="utf-8",
                    )
                    data_parent = workspace / "data"
                    data_parent.mkdir()
                    if self.sandbox:
                        (data_parent / "fineweb10B").mkdir()
                    else:
                        (data_parent / "fineweb10B").symlink_to(self.data_root / "fineweb10B")
                    (workspace / "logs").mkdir()
                    command = (
                        str(self.environment_python),
                        "-m",
                        "torch.distributed.run",
                        "--standalone",
                        f"--nproc_per_node={self.protocol.gpu_count}",
                        str(script),
                    )
                    if self.sandbox:
                        command = self._sandbox_command(command, workspace, script)
                    inherited = {
                        key: value
                        for key, value in os.environ.items()
                        if key
                        in {
                            "CUDA_HOME",
                            "CUDA_PATH",
                            "LD_LIBRARY_PATH",
                            "NVIDIA_DRIVER_CAPABILITIES",
                            "NVIDIA_VISIBLE_DEVICES",
                        }
                    }
                    environment = {
                        **inherited,
                        "CUDA_VISIBLE_DEVICES": ",".join(self.gpu_ids),
                        "HOME": "/tmp/home" if self.sandbox else str(workspace),
                        "LANG": "C.UTF-8",
                        "LC_ALL": "C.UTF-8",
                        "NCCL_SOCKET_IFNAME": "lo",
                        "PATH": (
                            f"{self.environment_python.parent}:"
                            f"{self.environment_python.resolve().parent}:/usr/bin:/bin"
                        ),
                        "PYTHONHASHSEED": str(seed),
                        "PYTHONDONTWRITEBYTECODE": "1",
                        "PYTHONNOUSERSITE": "1",
                        "TMPDIR": "/tmp" if self.sandbox else str(workspace),
                        "TORCHINDUCTOR_CACHE_DIR": "/tmp/torchinductor",
                        "TRITON_CACHE_DIR": "/tmp/triton",
                    }
                    stdout = self._execute(command, workspace, environment, effective_timeout)
                    score, loss, declared_steps = parse_validation(
                        stdout,
                        threshold=self.protocol.single_run_threshold,
                        penalty=self.protocol.failure_penalty_steps,
                        score_marker=score_marker,
                    )
                    trajectory, _ = validation_trajectory(stdout, score_marker=score_marker)
                    if declared_steps != train_steps:
                        raise AdapterError("Optimizer Design runtime train_steps differs from static policy")
                    result = OptimizerDesignEvaluation(
                        evaluation_id=evaluation_id,
                        status="completed",
                        score_valid=True,
                        score_steps=score,
                        target_reached=score != self.protocol.failure_penalty_steps,
                        val_loss=loss,
                        train_steps=train_steps,
                        seed=seed,
                        candidate_sha256=candidate_sha256,
                        stdout_sha256=hashlib.sha256(stdout.encode("utf-8")).hexdigest(),
                        wall_clock_seconds=time.monotonic() - started,
                        failure_reason=None,
                        validation_trajectory=trajectory,
                        **self._record_binding(),
                    )
        except (AdapterError, OSError, UnicodeError) as exc:
            result = OptimizerDesignEvaluation(
                evaluation_id=evaluation_id,
                status="failed",
                score_valid=False,
                score_steps=None,
                target_reached=False,
                val_loss=None,
                train_steps=None,
                seed=seed,
                candidate_sha256=candidate_sha256,
                stdout_sha256=hashlib.sha256(stdout.encode("utf-8")).hexdigest(),
                wall_clock_seconds=time.monotonic() - started,
                failure_reason=f"{type(exc).__name__}: {exc}",
                validation_trajectory=(),
                **self._record_binding(),
            )
        if sha256_file(candidate_path) != candidate_sha256:
            raise AdapterError("Optimizer Design evaluation mutated the immutable candidate artifact")
        (output_dir / "candidate.py").write_text(source, encoding="utf-8")
        (output_dir / "stdout.log").write_text(stdout, encoding="utf-8")
        write_json_exclusive(
            output_dir / "evaluation.json",
            {**asdict(result), "evaluation_digest": result.digest},
        )
        return result


__all__ = [
    "MAX_CANDIDATE_BYTES",
    "MAX_TRAIN_STEPS",
    "FROZEN_MODEL_SIGNATURE_SHA256",
    "OptimizerDesignEvaluation",
    "OptimizerDesignEvaluator",
    "inject_seed",
    "parse_validation",
    "score_validation_trajectories",
    "validation_trajectory",
    "validate_candidate",
]
