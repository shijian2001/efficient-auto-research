"""Shared runtime for isolated FML-bench external-agent adapters."""
from __future__ import annotations

import argparse
import json
import os
import signal
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Iterable, Optional

import yaml

from agents.base import AgentConfig, AgentResult, BaseAgent, StepResult
from benchmark.executor import BenchmarkExecutor
from benchmark.utils import extract_primary_metric, get_filtered_results_for_prompt

from source_editing import apply_strict_target_patch, build_source_edit_prompt


ADAPTER_ROOT = Path(__file__).resolve().parent
BENCHMARK_ADAPTERS_PARENT = ADAPTER_ROOT.parents[2]
if str(BENCHMARK_ADAPTERS_PARENT) not in sys.path:
    sys.path.insert(0, str(BENCHMARK_ADAPTERS_PARENT))

from BenchmarkAdapters.process import relay_client_env


PROXY_ENV_VARS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
)


@dataclass(frozen=True)
class StepCommand:
    argv: tuple[str, ...]
    cwd: Path
    stdin: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    response_path: Path | None = None
    timeout_seconds: int | None = None
    label: str = ""


def load_yaml(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _classify_failure(
    *,
    edit_ok: bool,
    command_ok: bool,
    changed_target_files: list[str],
    stdout_text: str,
    stderr_text: str,
    response_text: str,
    target_diff: str,
    val_result: dict[str, Any] | None,
    improved: bool,
    action: str,
) -> dict[str, Any]:
    text = "\n".join(
        part for part in (stderr_text or "", stdout_text or "", response_text or "") if part
    ).lower()
    val_result = val_result or {}
    category = "accepted" if improved else "not_accepted"
    reason = "candidate improved the configured validation metric" if improved else ""

    if "external agent timed out" in text or "timed out" in text or "timeout" in text:
        category = "timeout"
        reason = "external agent command timed out or reported a timeout"
    elif "stream disconnected" in text:
        category = "connection_interrupted"
        reason = "LLM relay stream disconnected before the agent completed"
    elif "connection refused" in text:
        category = "connection_refused"
        reason = "LLM relay or configured proxy refused the connection"
    elif "connection reset" in text or "connection aborted" in text or "connection closed" in text:
        category = "connection_interrupted"
        reason = "LLM relay connection was reset, aborted, or closed"
    elif "could not resolve host" in text or "name or service not known" in text:
        category = "dns_resolution_failed"
        reason = "relay hostname could not be resolved"
    elif "tls" in text and ("handshake" in text or "certificate" in text):
        category = "tls_failed"
        reason = "TLS handshake or certificate validation failed"
    elif "401" in text or "unauthorized" in text or "authentication is missing" in text:
        category = "auth_failed"
        reason = "API authentication failed or required key was missing"
    elif "429" in text or "rate limit" in text:
        category = "rate_limited"
        reason = "relay or upstream API returned a rate limit"
    elif "no unified diff" in text or "git apply" in text or "patch touches non-target" in text:
        category = "protocol_failed"
        reason = "agent response did not satisfy the patch/edit protocol"
    elif not command_ok and not changed_target_files:
        category = "agent_command_failed_no_edit"
        reason = "agent command failed before any target file changed"
    elif not changed_target_files:
        category = "no_target_change"
        reason = "agent completed or validation ran, but target file hashes did not change"
    elif not val_result.get("success"):
        category = "validation_failed"
        reason = str(val_result.get("error") or "validation command failed")
    elif not edit_ok:
        category = "edit_rejected"
        reason = "adapter did not accept the edit as successful"
    elif action == "switch":
        category = "metric_not_improved"
        reason = "target files changed and validation ran, but metric did not improve enough"

    return {
        "failure_category": category,
        "failure_reason": reason,
        "command_success": command_ok,
        "edit_success": edit_ok,
        "changed_target_file_count": len(changed_target_files),
        "has_target_diff": bool(target_diff.strip()),
    }


def _summary_diagnostics(steps: list[dict[str, Any]], test_result: dict[str, Any] | None) -> dict[str, Any]:
    categories: dict[str, int] = {}
    for step in steps:
        category = step.get("metadata", {}).get("failure_category", "unknown")
        categories[category] = categories.get(category, 0) + 1
    final_status = "ok"
    final_reason = ""
    if any(category not in {"accepted", "metric_not_improved"} for category in categories):
        final_status = "agent_or_adapter_issue"
        final_reason = "one or more agent edit steps failed before producing an accepted candidate"
    if test_result and not test_result.get("success"):
        final_status = "test_failed"
        final_reason = str(test_result.get("error") or "final test failed")
    return {
        "final_status": final_status,
        "final_reason": final_reason,
        "step_failure_categories": categories,
    }


def save_results(result: AgentResult, config: dict[str, Any], runner) -> None:
    agent_cfg = runner.agent.config
    task_cfg = runner.config
    val_steps = [asdict(step) for step in result.all_steps]
    summary = {
        "benchmark": runner.benchmark_name,
        "agent": getattr(agent_cfg.agent_type, "value", "external"),
        "model": agent_cfg.model,
        "provider": agent_cfg.provider,
        "agent_params": agent_cfg.agent_params,
        "task_config": {
            "metric": task_cfg.get("metric", ""),
            "metric_direction": task_cfg.get("metric_direction", ""),
            "conda_env": task_cfg.get("conda_env", ""),
            "repo_dir": task_cfg.get("repo_dir", ""),
            "target_files": task_cfg.get("target_files", []),
        },
        "best_val_metric": result.best_step.primary_metric if result.best_step else None,
        "test_result": result.test_result,
        "val_steps": val_steps,
        "diagnostics": _summary_diagnostics(val_steps, result.test_result),
        "token_usage": result.token_usage,
        "parent_workspace": result.parent_workspace,
        "total_duration_seconds": result.total_duration_seconds,
        "metadata": result.metadata,
    }
    save_path = Path(result.parent_workspace) / "summary.json"
    save_path.parent.mkdir(parents=True, exist_ok=True)
    save_path.write_text(json.dumps(summary, indent=2, default=str) + "\n", encoding="utf-8")
    (save_path.parent / "config_used.yaml").write_text(
        yaml.safe_dump(config, sort_keys=False), encoding="utf-8"
    )
    print(f"Summary saved to: {save_path}")


def print_summary(result: AgentResult) -> None:
    print(f"\n{'=' * 60}")
    print("Results Summary")
    print(f"{'=' * 60}")
    if result.best_step:
        print(
            f"Best Val Metric: {result.best_step.primary_metric}"
            f" (step {result.best_step.step_id}, idea: {result.best_step.idea_description})"
        )
    else:
        print("No successful validation runs.")

    if result.test_result and result.test_result.get("success"):
        print(f"Test Metric: {result.test_result.get('primary_metric')}")
    elif result.test_result:
        print(f"Test Failed: {result.test_result.get('error', 'unknown')}")
    for step in result.all_steps:
        metadata = step.metadata or {}
        category = metadata.get("failure_category", "unknown")
        reason = metadata.get("failure_reason", "")
        print(
            f"Step {step.step_id}: {category}"
            f" | edit_success={step.edit_success}"
            f" | command_success={metadata.get('command_success')}"
            f" | changed_targets={len(metadata.get('changed_target_files') or [])}"
        )
        if reason:
            print(f"  Reason: {reason}")
    print(f"Total Steps: {result.total_steps}")
    print(f"Total Ideas: {result.total_ideas}")
    print(f"{'=' * 60}")


def _resolve_metric_direction(executor_config: dict[str, Any]) -> str:
    direction = executor_config.get("metric_direction", "higher")
    return direction if direction in {"higher", "lower"} else "higher"


class ExternalBenchmarkAgent(BaseAgent):
    """Generic FML-bench agent for external CLI editors."""

    def __init__(self, config: AgentConfig, *, agent_name: str, display_name: str):
        super().__init__(config)
        self.agent_name = agent_name
        self.display_name = display_name
        params = config.agent_params
        if params.get("profile") != "smoke":
            raise ValueError("legacy FLM compatibility adapters support smoke profile only")
        self.max_steps = int(params.get("max_steps", 0))
        if self.max_steps != 1:
            raise ValueError("legacy FLM smoke profile requires max_steps=1")
        self.step_budget = self.max_steps
        wall_clock_seconds = params.get("wall_clock_seconds")
        if wall_clock_seconds is None or int(wall_clock_seconds) < 1:
            raise ValueError("legacy FLM smoke requires an explicit wall_clock_seconds")
        self.command_timeout_seconds = int(wall_clock_seconds)
        self.command_retry_attempts = max(1, int(params.get("command_retry_attempts", 1)))
        self.execute_timeout_seconds = int(wall_clock_seconds)
        self.response_mode = str(params.get("response_mode", "workspace"))
        command_env_var = str(params.get("command_env_var", "")).strip()
        command_from_env = os.environ.get(command_env_var, "") if command_env_var else ""
        self.command = command_from_env or params.get("command") or ""
        self.command_args = tuple(params.get("command_args", []))
        self.command_cwd = params.get("command_cwd")
        self.command_stdin = params.get("command_stdin", "prompt")
        self.command_output_file = params.get("command_output_file")
        self.command_env = dict(params.get("command_env", {}))
        self.use_relay_env = bool(params.get("use_relay_env", False))
        self.upstream_base_url = str(params.get("upstream_base_url", "")).strip()
        if self.use_relay_env and not self.upstream_base_url:
            raise ValueError("legacy FLM smoke requires an explicit upstream_base_url")
        self.proxy = str(params.get("proxy", "") or "")
        self.metric_tolerance = float(params.get("metric_tolerance", 1e-8))
        self.enforce_target_files = bool(params.get("enforce_target_files", True))
        self.source_context_max_chars = int(params.get("source_context_max_chars", 300_000))
        self.extra_prompt_suffix = str(params.get("prompt_suffix", "")).strip()
        self.require_auth = bool(params.get("require_auth", False))
        self.required_env = tuple(params.get("required_env", []))
        self.metric_history: list[dict[str, Any]] = []
        self._parent_workspace = ""

    def initialize(self) -> None:
        if not self.command:
            raise ValueError(f"{self.agent_name} command must not be empty")
        if isinstance(self.command, (list, tuple)):
            executable = str(self.command[0])
        else:
            executable = shlex.split(str(self.command))[0]
        if not shutil.which(executable) and not Path(executable).expanduser().is_file():
            raise ValueError(f"{self.agent_name} command is not available: {executable}")
        if self.require_auth:
            self._validate_auth()

    def _validate_auth(self) -> None:
        if self.required_env:
            missing = [name for name in self.required_env if not os.environ.get(name)]
            if missing:
                raise ValueError(
                    f"{self.agent_name} authentication is missing: set "
                    + ", ".join(missing)
                )
            return
        if not (
            os.environ.get("UPSTREAM_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
            or os.environ.get("ANTHROPIC_API_KEY")
        ):
            raise ValueError(
                f"{self.agent_name} authentication is missing: set UPSTREAM_API_KEY, "
                "OPENAI_API_KEY, or ANTHROPIC_API_KEY"
            )

    def build_prompt(self, step_id: int, baseline_results: dict[str, Any]) -> str:
        history = json.dumps(self.metric_history[-8:], indent=2, default=str)
        baseline = json.dumps(
            get_filtered_results_for_prompt(
                baseline_results,
                self.config.runtime_params.get("metrics", {}),
            ),
            indent=2,
            default=str,
        )
        targets = "\n".join(f"- {path}" for path in self.target_files)
        prompt = f"""You are the code-editing sub-agent inside an FML-bench run.

This is iteration {step_id}. Work only in the current repository.

## Task
{self.task_description}

## Target files
{targets}

## Metric
{self.metric_name} ({self.metric_direction} is better)

## Baseline validation result
```json
{baseline}
```

## Recent experiment history
```json
{history}
```

## Required behavior
- Inspect the existing implementation before editing.
- Propose and implement one focused, reproducible improvement.
- Modify only the target files listed above.
- Do not modify benchmark metadata, tests, hidden data, evaluation code, or .git.
- Do not run the benchmark's val_command or test_command yourself.
- Do not read results_tmp/test_info.json or any final-test output.
- Do not commit changes.
"""
        if self.extra_prompt_suffix:
            prompt += "\n\n" + self.extra_prompt_suffix + "\n"
        if self.response_mode == "patch":
            repo_dir = self.config.runtime_params["repo_dir"]
            return build_source_edit_prompt(
                prompt,
                repo_dir,
                self.target_files,
                max_chars=self.source_context_max_chars,
            )
        return prompt

    def build_step_command(
        self, prompt: str, step_id: int, step_dir: Path
    ) -> StepCommand:
        raise NotImplementedError

    def run(
        self,
        task_description: str,
        target_files: list[str],
        baseline_results: dict[str, Any],
    ) -> AgentResult:
        self.task_description = task_description or ""
        self.target_files = [os.path.abspath(path) for path in target_files or []]

        benchmark_config = self.config.runtime_params.get("benchmark_config", {})
        metrics_cfg = self.config.runtime_params.get("metrics", {})
        self.metric_name = benchmark_config.get("metric", "")
        self.metric_direction = _resolve_metric_direction(benchmark_config)
        self.baseline_primary_metric = extract_primary_metric(
            baseline_results or {},
            self.metric_name,
            metrics_cfg.get("include_datasets"),
        )

        agent_name = self.config.runtime_params.get("agent_name", self.agent_name)
        benchmark_name = self.config.runtime_params.get("benchmark_name", "benchmark")
        output_dir = self.config.runtime_params.get("output_dir", "benchmark_results")
        timestamp = f"{time.strftime('%Y%m%d_%H%M%S')}_{os.urandom(4).hex()}"
        self._parent_workspace = os.path.join(
            output_dir,
            agent_name,
            benchmark_name,
            timestamp,
        )
        os.makedirs(self._parent_workspace, exist_ok=True)

        timeout = self.execute_timeout_seconds
        self.executor = BenchmarkExecutor(
            benchmark_config,
            agent_name=agent_name,
            benchmark_name=benchmark_name,
            experiment_name=f"{timestamp}_{agent_name}",
            parent_timestamp=timestamp,
            timeout=timeout,
            output_dir=output_dir,
        )
        self.executor.setup_workspace()
        self.best_code_snapshot = self._snapshot_target_files()
        self.best_metric = self.baseline_primary_metric
        all_steps: list[StepResult] = []

        try:
            while self.budget_remaining():
                step_id = self.step_count + 1
                previous_best_metric = self.best_metric
                previous_best_snapshot = self.best_code_snapshot
                self._remove_results_tmp()
                repo_dir = self.config.runtime_params["repo_dir"]
                prompt = self.build_prompt(step_id, baseline_results or {})
                step_dir = Path(self._parent_workspace) / f"{self.agent_name}_step_{step_id:04d}"
                step_dir.mkdir(parents=True, exist_ok=True)
                before_hashes = self._target_hashes()

                stdout_chunks: list[str] = []
                stderr_chunks: list[str] = []
                response_text = ""
                command_ok = False
                command_attempts = 0
                for command_attempts in range(1, self.command_retry_attempts + 1):
                    command = self.build_step_command(prompt, step_id, step_dir)
                    command_ok, stdout_text, stderr_text, response_text = self._run_step(command)
                    stdout_chunks.append(stdout_text)
                    stderr_chunks.append(stderr_text)
                    attempt_hashes = self._target_hashes()
                    attempt_changed_target_files = [
                        path
                        for path, digest in attempt_hashes.items()
                        if before_hashes.get(path) != digest
                    ]
                    if (
                        command_ok
                        or attempt_changed_target_files
                        or command_attempts >= self.command_retry_attempts
                        or not self._is_transient_command_error(stderr_text)
                    ):
                        break
                    stderr_chunks.append(
                        f"{self.display_name} command failed before editing; "
                        f"retrying transient error ({command_attempts}/{self.command_retry_attempts})."
                    )
                stdout_text = "\n".join(chunk for chunk in stdout_chunks if chunk)
                stderr_text = "\n".join(chunk for chunk in stderr_chunks if chunk)
                edit_ok = command_ok

                if self.response_mode == "patch":
                    if response_text:
                        apply_ok, apply_message, hypothesis = apply_strict_target_patch(
                            response_text,
                            repo_dir,
                            self.target_files,
                        )
                        edit_ok = edit_ok and apply_ok
                        stdout_text = (
                            stdout_text
                            + "\n"
                            + f"Hypothesis: {hypothesis or 'not provided'}\n"
                            + f"Apply result: {apply_message}"
                        )
                after_hashes = self._target_hashes()
                changed_target_files = [
                    path
                    for path, digest in after_hashes.items()
                    if before_hashes.get(path) != digest
                ]
                if self.response_mode == "workspace" and changed_target_files:
                    edit_ok = True
                target_diff = self._target_diff()
                step_artifacts = self._write_step_artifacts(
                    step_dir=step_dir,
                    stdout_text=stdout_text,
                    stderr_text=stderr_text,
                    response_text=response_text,
                    target_diff=target_diff,
                )
                if self.enforce_target_files:
                    outside = self._dirty_outside_targets()
                    if outside:
                        self._restore_non_target_changes(outside)

                candidate_snapshot = self._snapshot_target_files()
                val_result = self._execute_val(run_id=step_id)
                candidate_metric = val_result.get("primary_metric")
                improved = (
                    edit_ok
                    and bool(changed_target_files)
                    and val_result.get("success")
                    and candidate_metric is not None
                    and self._metric_change_is_meaningful(candidate_metric, previous_best_metric)
                    and self._should_update_best(candidate_metric, previous_best_metric)
                )
                if improved:
                    self.best_metric = candidate_metric
                    self.best_code_snapshot = candidate_snapshot
                    action = "improve"
                else:
                    if previous_best_snapshot:
                        self._restore_snapshot(previous_best_snapshot)
                    self.best_metric = previous_best_metric
                    self.best_code_snapshot = previous_best_snapshot
                    action = "debug" if not val_result.get("success") else "switch"
                step_diagnostics = _classify_failure(
                    edit_ok=edit_ok,
                    command_ok=command_ok,
                    changed_target_files=changed_target_files,
                    stdout_text=stdout_text,
                    stderr_text=stderr_text,
                    response_text=response_text,
                    target_diff=target_diff,
                    val_result=val_result,
                    improved=improved,
                    action=action,
                )

                all_steps.append(
                    StepResult(
                        step_id=step_id,
                        idea_id=f"{self.agent_name}_{step_id}",
                        idea_description=(stdout_text or "").strip()[-1000:],
                        action=action,
                        edit_success=edit_ok,
                        val_result=val_result,
                        primary_metric=candidate_metric,
                        step_duration_seconds=self._last_val_duration,
                        metadata={
                            "agent": self.agent_name,
                            "metric_name": self.metric_name,
                            "metric_direction": self.metric_direction,
                            "changed_target_files": changed_target_files,
                            "command_success": command_ok,
                            "command_attempts": command_attempts,
                            "step_artifacts": step_artifacts,
                            "target_diff_tail": target_diff[-2000:],
                            **step_diagnostics,
                            "stdout_tail": stdout_text[-2000:],
                            "stderr_tail": stderr_text[-2000:],
                            "response_mode": self.response_mode,
                        },
                    )
                )
                self.metric_history.append(
                    {
                        "step": step_id,
                        "metric": candidate_metric,
                        "success": bool(val_result.get("success")),
                        "accepted": improved,
                        "edit_success": edit_ok,
                        "changed_target_files": changed_target_files,
                        "action": action,
                        "failure_category": step_diagnostics["failure_category"],
                        "failure_reason": step_diagnostics["failure_reason"],
                    }
                )

            if self.best_code_snapshot:
                self._restore_snapshot(self.best_code_snapshot)
            test_result = self._execute_test()
        finally:
            if self.executor is not None:
                self.executor.cleanup()

        best_step = self._find_best_step(all_steps)
        return AgentResult(
            all_steps=all_steps,
            best_step=best_step,
            test_result=test_result,
            total_steps=self.step_count,
            total_ideas=len(all_steps),
            token_usage={},
            parent_workspace=self._parent_workspace,
            metadata={
                "metric_history": self.metric_history,
                "token_usage_available": False,
                "profile": "smoke",
                "non_formal": True,
                "non_comparable": True,
            },
        )

    def _run_step(self, command: StepCommand) -> tuple[bool, str, str, str]:
        process_group_kwargs = {}
        if os.name == "nt":
            process_group_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            process_group_kwargs["start_new_session"] = True
        env = self._subprocess_env(command.env)
        process = None
        stdout = ""
        stderr = ""
        try:
            process = subprocess.Popen(
                list(command.argv),
                cwd=str(command.cwd),
                env=env,
                text=True,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                **process_group_kwargs,
            )
            stdout, stderr = process.communicate(
                input=command.stdin,
                timeout=command.timeout_seconds or self.command_timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            if process is not None:
                self._terminate_process_group(process)
                stdout, stderr = process.communicate()
            stderr = (stderr or "") + "\nexternal agent timed out"
            return False, stdout or "", stderr or "", ""
        except OSError as exc:
            return False, "", str(exc), ""

        response = stdout or ""
        if command.response_path and command.response_path.exists():
            response = command.response_path.read_text(encoding="utf-8", errors="replace")
        return process.returncode == 0, stdout or "", stderr or "", response

    @staticmethod
    def _subprocess_env(command_env: dict[str, str]) -> dict[str, str]:
        env = {key: value for key, value in os.environ.items() if key not in PROXY_ENV_VARS}
        env.update(command_env)
        for name in PROXY_ENV_VARS:
            if not env.get(name):
                env.pop(name, None)
        return env

    @staticmethod
    def _terminate_process_group(process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        if os.name == "nt":
            process.terminate()
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        except OSError:
            process.terminate()
            return
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                return
            except OSError:
                process.kill()

    @staticmethod
    def _is_transient_command_error(stderr: str) -> bool:
        text = (stderr or "").lower()
        markers = (
            "stream disconnected",
            "reconnecting",
            "connection reset",
            "connection refused",
            "connection aborted",
            "connection closed",
            "timed out",
            "timeout",
            "temporarily unavailable",
            "error sending request",
        )
        return any(marker in text for marker in markers)

    def _metric_change_is_meaningful(
        self, candidate_metric: float, previous_metric: Optional[float]
    ) -> bool:
        if previous_metric is None:
            return True
        tolerance = self.metric_tolerance
        if self.metric_direction == "higher":
            return float(candidate_metric) > float(previous_metric) + tolerance
        return float(candidate_metric) < float(previous_metric) - tolerance

    def _remove_results_tmp(self) -> None:
        repo_dir = self.config.runtime_params.get("repo_dir")
        if not repo_dir:
            return
        results_tmp = Path(repo_dir) / "results_tmp"
        if results_tmp.exists():
            shutil.rmtree(results_tmp)

    def _target_hashes(self) -> dict[str, Optional[str]]:
        import hashlib

        hashes: dict[str, Optional[str]] = {}
        for target in self.target_files:
            path = Path(target)
            if not path.is_file():
                hashes[str(path)] = None
                continue
            hashes[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
        return hashes

    def _git_status_paths(self) -> set[str]:
        repo_dir = self.config.runtime_params.get("repo_dir", "")
        result = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=repo_dir,
            text=True,
            capture_output=True,
            check=False,
        )
        paths = set()
        for line in result.stdout.splitlines():
            if len(line) >= 4:
                paths.add(line[3:].split(" -> ")[-1])
        return paths

    def _target_diff(self) -> str:
        repo_dir = self.config.runtime_params.get("repo_dir", "")
        if not repo_dir:
            return ""
        target_names = [
            os.path.normpath(os.path.relpath(path, repo_dir))
            for path in self.target_files
        ]
        result = subprocess.run(
            ["git", "diff", "--", *target_names],
            cwd=repo_dir,
            text=True,
            capture_output=True,
            check=False,
        )
        return result.stdout or ""

    @staticmethod
    def _write_step_artifacts(
        *,
        step_dir: Path,
        stdout_text: str,
        stderr_text: str,
        response_text: str,
        target_diff: str,
    ) -> dict[str, str]:
        artifacts = {
            "stdout": "stdout.txt",
            "stderr": "stderr.txt",
            "response": "response.txt",
            "target_diff": "target.diff",
        }
        payloads = {
            "stdout.txt": stdout_text or "",
            "stderr.txt": stderr_text or "",
            "response.txt": response_text or "",
            "target.diff": target_diff or "",
        }
        step_dir.mkdir(parents=True, exist_ok=True)
        for name, payload in payloads.items():
            (step_dir / name).write_text(payload, encoding="utf-8", errors="replace")
        return {key: str((step_dir / filename).resolve()) for key, filename in artifacts.items()}

    def _dirty_outside_targets(self) -> list[str]:
        repo_dir = self.config.runtime_params.get("repo_dir", "")
        target_names = {
            os.path.normpath(os.path.relpath(path, repo_dir))
            for path in self.target_files
        }
        return sorted(
            path
            for path in self._git_status_paths()
            if os.path.normpath(path) not in target_names
        )

    def _restore_non_target_changes(self, paths: list[str]) -> None:
        repo_dir = self.config.runtime_params.get("repo_dir", "")
        tracked_paths: list[str] = []
        untracked_paths: list[str] = []
        for path in paths:
            result = subprocess.run(
                ["git", "ls-files", "--error-unmatch", "--", path],
                cwd=repo_dir,
                text=True,
                capture_output=True,
                check=False,
            )
            if result.returncode == 0:
                tracked_paths.append(path)
            else:
                untracked_paths.append(path)
        if tracked_paths:
            subprocess.run(
                ["git", "restore", "--staged", "--worktree", "--", *tracked_paths],
                cwd=repo_dir,
                text=True,
                capture_output=True,
                check=False,
            )
        if untracked_paths:
            subprocess.run(
                ["git", "clean", "-fd", "--", *untracked_paths],
                cwd=repo_dir,
                text=True,
                capture_output=True,
                check=False,
            )

    @staticmethod
    def _find_best_step(steps: list[StepResult]) -> Optional[StepResult]:
        valid = [step for step in steps if step.primary_metric is not None]
        if not valid:
            return None
        direction = steps[0].metadata.get("metric_direction", "higher")
        return max(valid, key=lambda step: step.primary_metric) if direction == "higher" else min(valid, key=lambda step: step.primary_metric)


class ConfigDrivenExternalBenchmarkAgent(ExternalBenchmarkAgent):
    """External agent whose command is fully described by config fields."""

    def build_step_command(
        self, prompt: str, step_id: int, step_dir: Path
    ) -> StepCommand:
        repo_dir = Path(self.config.runtime_params["repo_dir"]).resolve()
        response_path = step_dir / "last_message.txt"
        command = self._command_argv(
            prompt=prompt,
            response_path=response_path,
            step_dir=step_dir,
            repo_dir=repo_dir,
        )
        stdin = prompt if str(self.command_stdin).lower() in {"prompt", "stdin", "true"} else None
        if self.response_mode == "patch" and not command.response_path:
            command = StepCommand(
                argv=command.argv,
                cwd=command.cwd,
                stdin=command.stdin,
                env=command.env,
                response_path=response_path,
                timeout_seconds=command.timeout_seconds,
                label=command.label,
            )
        return StepCommand(
            argv=command.argv,
            cwd=command.cwd,
            stdin=stdin if command.stdin is None else command.stdin,
            env=command.env,
            response_path=command.response_path,
            timeout_seconds=command.timeout_seconds,
            label=command.label,
        )

    def _command_argv(
        self,
        *,
        prompt: str,
        response_path: Path,
        step_dir: Path,
        repo_dir: Path,
    ) -> StepCommand:
        base = shlex.split(str(self.command)) if isinstance(self.command, str) else [str(item) for item in self.command]
        base = [
            self._format_token(
                token,
                prompt=prompt,
                response_path=response_path,
                step_dir=step_dir,
                repo_dir=repo_dir,
            )
            for token in base
        ]
        extra = [
            self._format_token(
                token,
                prompt=prompt,
                response_path=response_path,
                step_dir=step_dir,
                repo_dir=repo_dir,
            )
            for token in self.command_args
        ]
        argv = tuple(base + extra)
        cwd = Path(
            self._format_token(
                str(self.command_cwd or repo_dir),
                prompt=prompt,
                response_path=response_path,
                step_dir=step_dir,
                repo_dir=repo_dir,
            )
        )
        env = (
            relay_client_env(
                base_url=self.upstream_base_url,
                proxy=self.proxy,
                model=str(self.config.model),
            )
            if self.use_relay_env
            else {}
        )
        env.update({
            key: self._format_token(
                value,
                prompt=prompt,
                response_path=response_path,
                step_dir=step_dir,
                repo_dir=repo_dir,
            )
            for key, value in self.command_env.items()
        })
        formatted_response = self.command_output_file
        if formatted_response:
            formatted_response = self._format_token(
                str(formatted_response),
                prompt=prompt,
                response_path=response_path,
                step_dir=step_dir,
                repo_dir=repo_dir,
            )
        response = Path(formatted_response) if formatted_response else (response_path if self.response_mode == "patch" else None)
        return StepCommand(
            argv=argv,
            cwd=cwd,
            env=env,
            response_path=response,
            timeout_seconds=self.command_timeout_seconds,
            label=self.agent_name,
        )

    def _format_token(
        self,
        token: str,
        *,
        prompt: str,
        response_path: Path,
        step_dir: Path,
        repo_dir: Path,
    ) -> str:
        replacements = {
            "prompt": prompt,
            "response_path": str(response_path),
            "step_dir": str(step_dir),
            "repo_dir": str(repo_dir),
            "target_files": os.pathsep.join(getattr(self, "target_files", [])),
            "target_files_json": json.dumps(getattr(self, "target_files", [])),
            "model": str(self.config.model),
            "provider": str(self.config.provider),
            "upstream_base_url": self.upstream_base_url,
            "proxy": self.proxy,
            "agent_name": self.agent_name,
            "display_name": self.display_name,
        }
        replacements.update(
            {
                str(key): str(value)
                for key, value in self.config.agent_params.items()
                if isinstance(value, (str, int, float, bool))
            }
        )
        try:
            return str(token).format(**replacements)
        except Exception:
            return str(token)


def _load_fml_root(argv: list[str] | None = None) -> Path:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--fml-root")
    known, _ = parser.parse_known_args(argv)
    configured = known.fml_root or os.environ.get("FML_BENCH_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    raise ValueError("set --fml-root or FML_BENCH_ROOT")


def _merged_agent_config(agent_name: str, agent_cfg: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    params = dict(agent_cfg.get(agent_name, {}))
    params.update(overrides)
    return params


def _agent_model(agent_name: str, agent_cfg: dict[str, Any]) -> str:
    nested = agent_cfg.get(agent_name, {})
    if isinstance(nested, dict) and nested.get("model"):
        return str(nested["model"])
    model = str(agent_cfg.get("model", "")).strip()
    if not model:
        raise ValueError(f"{agent_name} requires an explicit model")
    return model


def _agent_provider(agent_name: str, agent_cfg: dict[str, Any]) -> str:
    nested = agent_cfg.get(agent_name, {})
    if isinstance(nested, dict) and nested.get("provider"):
        return str(nested["provider"])
    return str(agent_cfg.get("provider", "OpenAI"))


def _apply_cli_agent_overrides(
    agent_name: str,
    agent_cfg: dict[str, Any],
    *,
    model: str | None,
    provider: str | None,
) -> None:
    nested = agent_cfg.setdefault(agent_name, {})
    if not isinstance(nested, dict):
        raise ValueError(f"agent config for {agent_name} must be a mapping")
    if model:
        agent_cfg["model"] = model
        nested["model"] = model
    if provider:
        agent_cfg["provider"] = provider
        nested["provider"] = provider


def run_adapter_cli(
    *,
    agent_name: str,
    display_name: str,
    agent_cls: Callable[[AgentConfig], ExternalBenchmarkAgent],
    argv: list[str] | None = None,
) -> int:
    parser = argparse.ArgumentParser(description=display_name)
    parser.add_argument("--fml-root", default=os.environ.get("FML_BENCH_ROOT"))
    parser.add_argument("--agent-config", required=True)
    parser.add_argument("--task-config", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--provider", default=None)
    parser.add_argument("--relay-base-url", required=True)
    parser.add_argument("--wall-clock-seconds", type=int, required=True)
    parser.add_argument("--workspace-label", default=None)
    parser.add_argument("--output-dir", default="benchmark_results")
    args = parser.parse_args(argv)

    fml_root = Path(args.fml_root).expanduser().resolve() if args.fml_root else _load_fml_root(argv)
    if not (fml_root / "benchmark" / "runner.py").is_file():
        raise FileNotFoundError(f"Invalid FML-bench root: {fml_root}")
    if str(fml_root) not in sys.path:
        sys.path.insert(0, str(fml_root))

    from benchmark.runner import BenchmarkRunner

    original_cwd = Path.cwd()
    os.chdir(fml_root)
    agent_cfg = load_yaml(args.agent_config).get("agent", {})
    task_cfg = load_yaml(args.task_config)
    _apply_cli_agent_overrides(
        agent_name,
        agent_cfg,
        model=args.model,
        provider=args.provider,
    )
    benchmark_name = task_cfg.get("benchmark", {}).get("name")
    if not benchmark_name:
        raise ValueError("task config does not define benchmark.name")

    agent_config = AgentConfig(
        agent_type=SimpleNamespace(value=agent_name),
        model=_agent_model(agent_name, agent_cfg),
        provider=_agent_provider(agent_name, agent_cfg),
        agent_params=_merged_agent_config(
            agent_name,
            agent_cfg,
            {
                "profile": "smoke",
                "upstream_base_url": args.relay_base_url,
                "wall_clock_seconds": args.wall_clock_seconds,
            },
        ),
        runtime_params={
            "metrics": task_cfg.get("metrics", {}),
            "benchmark_config": task_cfg.get("benchmark", {}),
            "agent_name": agent_name,
            "benchmark_name": benchmark_name,
            "output_dir": args.output_dir,
        },
    )
    agent = agent_cls(agent_config)
    agent.initialize()
    try:
        runner = BenchmarkRunner(
            benchmark_name,
            agent,
            workspace_label=args.workspace_label,
            output_dir=args.output_dir,
        )
    except Exception:
        os.chdir(original_cwd)
        raise
    try:
        result = runner.run()
        save_results(result, {"agent": agent_cfg, **task_cfg}, runner)
        print_summary(result)
        return 0
    finally:
        agent.kill_running_process()
        runner.cleanup_workspace()
        os.chdir(original_cwd)
