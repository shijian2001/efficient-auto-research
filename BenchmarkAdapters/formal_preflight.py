"""Structured fail-closed preflight for one formal benchmark/Agent cell."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .AutoResearch.protocol import AutoResearchProtocol, BaselineScoreRecord as ARBaseline
from .contracts import AdapterError
from .FMLBench.protocol import FMLProtocol
from .formal_contract import FormalPreflightReport, ModelTrackConfig, PreflightCheck
from .MLEBenchLite.campaign import validate_mle_protocol
from .OptimizerDesign.protocol import BaselineScoreRecord as OptimizerBaseline
from .OptimizerDesign.protocol import OptimizerDesignProtocol
from .OptimizerDesign.runtime import AgentRuntimeManifest
from .protocol import FormalProtocol, sha256_file
from .registry import AGENTS, ROOT
from .task_specs import task_spec_digest
from .TerminalAO.protocol import TerminalAOProtocol


def git_identity(path: Path) -> tuple[str | None, bool | None]:
    resolved = path.resolve()
    commit = subprocess.run(
        ["git", "-C", str(resolved), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    dirty = subprocess.run(
        ["git", "-C", str(resolved), "status", "--porcelain", "--untracked-files=normal"],
        capture_output=True,
        text=True,
        check=False,
    )
    if commit.returncode or dirty.returncode:
        return None, None
    return commit.stdout.strip(), bool(dirty.stdout.strip())


def _check(name: str, callback) -> PreflightCheck:
    try:
        detail = callback()
        return PreflightCheck(name=name, passed=True, detail=str(detail or "verified"))
    except Exception as exc:
        return PreflightCheck(name=name, passed=False, detail=f"{type(exc).__name__}: {exc}")


def _require_commit(value: str | None, description: str) -> str:
    if value is None or len(value) != 40 or any(
        character not in "0123456789abcdef" for character in value.lower()
    ):
        raise AdapterError(f"{description} immutable commit is missing")
    return value


def _require_executable(value: str | Path, description: str) -> str:
    path = Path(value).expanduser()
    resolved = path.resolve() if path.is_absolute() or "/" in str(value) else None
    executable = str(resolved) if resolved is not None else shutil.which(str(value))
    if executable is None or not Path(executable).is_file() or not os.access(executable, os.X_OK):
        raise AdapterError(f"{description} executable is unavailable: {value}")
    return str(Path(executable).resolve())


def _registered_launcher_runtime(benchmark_id: str, agent_id: str, protocol: object) -> str:
    if benchmark_id == "optimizer-design":
        manifest = AgentRuntimeManifest.load(protocol.agent_runtime_manifest_path)
        manifest.validate(agent_id)
        return manifest.agents[agent_id].executable_path
    if benchmark_id == "mle-bench-lite":
        _require_executable("docker" if agent_id in {"ear", "mlevolve", "arbor"} else "bwrap", "MLE sandbox")
        if agent_id == "codex":
            _require_executable("codex", "Codex")
        elif agent_id == "claude-code":
            _require_executable("claude", "Claude Code")
        elif agent_id == "ml-master-2":
            _require_executable(ROOT / "baselines/EvoMaster/.venv/bin/python", "ML-Master 2")
        elif agent_id == "ai-scientist":
            _require_executable(ROOT / "baselines/AiScientist/.venv/bin/aisci", "AiScientist")
        _require_executable(ROOT / "BenchmarkAdapters/.venv/bin/python", "adapter runtime")
        return "isolated MLE runtime"
    if benchmark_id == "terminal-bench-ao":
        runtimes: dict[str, str | Path] = {
            "ear": ROOT / "BenchmarkAdapters/environments/mle/ear/.venv/bin/python",
            "mlevolve": ROOT / "BenchmarkAdapters/environments/agents/mlevolve/.venv/bin/python",
            "arbor": ROOT / "baselines/Arbor/.venv/bin/arbor",
            "codex": "codex",
            "claude-code": "claude",
            "ml-master-2": ROOT / "baselines/EvoMaster/.venv/bin/python",
            "ai-scientist": ROOT / "baselines/AiScientist/.venv/bin/python",
        }
        _require_executable(ROOT / "BenchmarkAdapters/.venv/bin/python", "Terminal AO adapter runtime")
        return _require_executable(runtimes[agent_id], f"Terminal AO {agent_id}")
    if benchmark_id == "autoresearch-architecture":
        runtimes = {
            "ear": ROOT / "BenchmarkAdapters/environments/agents/ear-autoresearch/.venv/bin/python",
            "mlevolve": ROOT / "BenchmarkAdapters/environments/agents/mlevolve-autoresearch/.venv/bin/python",
            "arbor": ROOT / "BenchmarkAdapters/environments/terminal/arbor/.venv/bin/python",
            "codex": "codex",
            "claude-code": "claude",
            "ml-master-2": ROOT / "BenchmarkAdapters/environments/agents/ml-master-2-autoresearch/.venv/bin/python",
            "ai-scientist": ROOT / "BenchmarkAdapters/environments/terminal/ai-scientist/.venv/bin/python",
        }
        return _require_executable(runtimes[agent_id], f"Autoresearch {agent_id}")
    raise AdapterError(f"unsupported launcher runtime check: {benchmark_id}")


def collect_formal_preflight(
    *,
    benchmark_id: str,
    agent_id: str,
    agent_variant: str,
    protocol_path: Path,
    model_config_path: Path,
    formal: bool,
    data_root: Path | None = None,
) -> FormalPreflightReport:
    if agent_id not in AGENTS:
        raise AdapterError(f"unknown formal Agent: {agent_id}")
    adapter_commit, adapter_dirty = git_identity(ROOT)
    agent_commit, agent_dirty = git_identity(AGENTS[agent_id].install_path)
    state: dict[str, Any] = {}

    def protocol_state() -> object:
        value = state.get("protocol")
        if value is None:
            raise AdapterError("blocked because protocol_assets_verified failed")
        return value

    def model_state() -> ModelTrackConfig:
        value = state.get("model_config")
        if value is None:
            raise AdapterError("blocked because model_config_complete failed")
        return value

    def model_config() -> str:
        config = ModelTrackConfig.load(
            model_config_path,
            formal=formal,
            require_terminal_inner=benchmark_id == "terminal-bench-ao",
        )
        if benchmark_id == "fml-bench" and formal:
            if config.request_timeout_seconds is None:
                raise AdapterError("formal FML requires an explicit model request timeout")
            if not config.retry_policy:
                raise AdapterError("formal FML requires an explicit model retry policy")
        state["model_config"] = config
        return config.digest

    def protocol() -> str:
        if benchmark_id == "mle-bench-lite":
            value = FormalProtocol.load(protocol_path)
            if data_root is None:
                raise AdapterError("MLE formal preflight requires data_root")
            validate_mle_protocol(value, data_root)
            state["benchmark_commit"] = json.loads(
                (ROOT / "BenchmarkAdapters/MLEBenchLite/data_manifest.json").read_text(
                    encoding="utf-8"
                )
            )["mlebench_source_commit"]
        elif benchmark_id == "terminal-bench-ao":
            value = TerminalAOProtocol.load(protocol_path)
            if formal:
                value.require_formal_contract()
            state["benchmark_commit"] = value.benchmark_source_commit
        elif benchmark_id == "autoresearch-architecture":
            value = AutoResearchProtocol.load(protocol_path)
            if formal:
                value.require_formal_contract()
            state["benchmark_commit"] = __import__(
                "BenchmarkAdapters.AutoResearch.baseline", fromlist=["BaselineManifest"]
            ).BaselineManifest.load(value.baseline_manifest_path).source_commit
        elif benchmark_id == "optimizer-design":
            value = OptimizerDesignProtocol.load(protocol_path)
            if formal:
                value.require_formal_contract()
            state["benchmark_commit"] = __import__(
                "BenchmarkAdapters.OptimizerDesign.protocol", fromlist=["SourceManifest"]
            ).SourceManifest.load(value.source_manifest_path).source_commit
        elif benchmark_id == "fml-bench":
            value = FMLProtocol.load(protocol_path, formal=formal)
            state["benchmark_commit"] = value.upstream_commit
        else:
            raise AdapterError(f"unknown formal benchmark: {benchmark_id}")
        state["protocol"] = value
        return value.digest

    def baseline() -> str:
        value = protocol_state()
        if benchmark_id == "autoresearch-architecture":
            record = ARBaseline.load(value.baseline_score_record_path)
            if formal and record.status != "completed":
                raise AdapterError("Autoresearch baseline status is pending")
            return record.status
        if benchmark_id == "optimizer-design":
            record = OptimizerBaseline.load(value.baseline_score_record_path)
            if formal and record.status != "completed":
                raise AdapterError("Optimizer Design baseline status is pending")
            return record.status
        return "not-required"

    def launcher() -> str:
        value = protocol_state()
        if benchmark_id == "fml-bench":
            from .FMLBench.agents import get_fml_agent_adapter

            adapter = get_fml_agent_adapter(agent_id)
            report = adapter.validate_installation()
            if not report.ready:
                raise AdapterError(report.failure_reason or f"FML {agent_id} runtime is unavailable")
            if agent_id not in value.agent_adapter_ids:
                raise AdapterError(f"FML protocol omitted concrete adapter: {agent_id}")
            return f"{adapter.native_entrypoint}: {report.executable_path}"
        backend = {
            "mle-bench-lite": AGENTS[agent_id].mle_backend,
            "terminal-bench-ao": AGENTS[agent_id].terminal_ao_backend,
            "autoresearch-architecture": AGENTS[agent_id].autoresearch_backend,
            "optimizer-design": AGENTS[agent_id].optimizer_design_backend,
        }[benchmark_id]
        if not backend or backend.startswith("blocked:"):
            raise AdapterError(f"formal launcher backend is unavailable: {agent_id}")
        runtime = _registered_launcher_runtime(benchmark_id, agent_id, value)
        return f"{backend}: {runtime}"

    def hardware() -> str:
        value = protocol_state()
        if benchmark_id == "optimizer-design":
            if value.gpu_count != 4:
                raise AdapterError("Optimizer Design requires four GPUs per evaluation")
            return "4xH100; max_concurrent_evaluations=1"
        if benchmark_id in {"autoresearch-architecture", "fml-bench"}:
            if benchmark_id == "fml-bench" and "H100" not in value.gpu_type:
                raise AdapterError("FML hardware profile must be H100")
            return "H100 profile"
        return "RTX 4090 profile"

    def task_spec() -> str:
        expected = task_spec_digest(benchmark_id)
        value = protocol_state()
        assets = (
            value.asset_digests
            if benchmark_id == "mle-bench-lite"
            else value.protocol_asset_digests
            if benchmark_id == "fml-bench"
            else value.protocol_asset_digests()
        )
        if assets.get("task_spec") != expected:
            raise AdapterError("protocol task specification digest differs from canonical task")
        return expected

    def baseline_digest() -> str:
        value = protocol_state()
        if benchmark_id not in {"autoresearch-architecture", "optimizer-design"}:
            return "not-required"
        path = value.baseline_score_record_path
        expected = value.baseline_score_record_digest
        actual = sha256_file(path)
        if actual != expected:
            raise AdapterError(
                f"baseline digest mismatch: expected={expected} actual={actual}"
            )
        return actual

    checks = (
        PreflightCheck(
            "agent_variant_explicit",
            not formal
            or bool(agent_variant.strip() and agent_variant.strip().lower() != "default"),
            agent_variant or "missing",
        ),
        PreflightCheck(
            "formal_source_clean",
            not formal or (adapter_dirty is False and agent_dirty is False),
            f"adapter_dirty={adapter_dirty} agent_dirty={agent_dirty}",
        ),
        PreflightCheck(
            "agent_source_commit_present",
            agent_commit is not None and len(agent_commit) == 40,
            str(agent_commit),
        ),
        PreflightCheck(
            "adapter_source_commit_present",
            adapter_commit is not None and len(adapter_commit) == 40,
            str(adapter_commit),
        ),
        _check("model_config_complete", model_config),
        _check("protocol_assets_verified", protocol),
        _check(
            "benchmark_source_commit_present",
            lambda: _require_commit(state.get("benchmark_commit"), "benchmark"),
        ),
        _check("dataset_digest_verified", lambda: protocol_state().digest),
        _check("baseline_status", baseline),
        _check("baseline_digest_verified", baseline_digest),
        _check("launcher_config_complete", launcher),
        _check("hardware_profile_valid", hardware),
        _check("task_spec_digest_verified", task_spec),
        _check(
            "external_repo_source_clean",
            lambda: (
                "not-applicable"
                if benchmark_id != "fml-bench"
                else "smoke-non-formal"
                if not formal
                else "clean"
                if git_identity(protocol_state().upstream_root)[1] is False
                else (_ for _ in ()).throw(
                    AdapterError("formal FML upstream repository is dirty")
                )
            ),
        ),
    )
    report = FormalPreflightReport(
        schema_version=1,
        benchmark_id=benchmark_id,
        formal=formal,
        checks=checks,
    )
    return report


__all__ = ["collect_formal_preflight", "git_identity"]
