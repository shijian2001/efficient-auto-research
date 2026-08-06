"""Shared repository optimization loop used by research Agent launchers."""

from __future__ import annotations

import json
import re
import shutil
import tempfile
import time
from dataclasses import asdict
from pathlib import Path

from ..contracts import AdapterError, require_directory, require_file
from .client import OpenAICompatibleClient
from .contracts import CandidateRecord, EvaluationResult, RepositoryAgentRequest
from .profiles import get_profile
from .revisions import RevisionStore
from .sandbox import RepositorySandbox


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List repository files matching a glob pattern.",
            "parameters": {
                "type": "object",
                "properties": {"pattern": {"type": "string"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a UTF-8 repository file with numbered lines.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "offset": {"type": "integer", "minimum": 1},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 2000},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create or replace a UTF-8 file inside the repository.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "replace_in_file",
            "description": "Replace exactly one literal text occurrence in a repository file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old": {"type": "string"},
                    "new": {"type": "string"},
                },
                "required": ["path", "old", "new"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_shell",
            "description": "Run a shell command in the isolated repository without network or credentials.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "timeout_seconds": {"type": "integer", "minimum": 1},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "evaluate_dev",
            "description": "Run the immutable development evaluator and return objective feedback.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit_candidate",
            "description": "Finish this candidate and submit its current repository state.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


class RepositoryAgentBackend:
    def __init__(self, request: RepositoryAgentRequest, *, client=None):
        self.request = request
        self.repository = require_directory(request.repository, "repository Harness")
        self.evaluator = require_file(request.evaluator, "development evaluator")
        self.dev_data = require_file(request.dev_data, "development split")
        self.output_dir = request.output_dir.resolve()
        try:
            self.output_dir.relative_to(self.repository)
        except ValueError:
            pass
        else:
            raise AdapterError("repository Agent output_dir must be outside the optimized repository")
        if request.candidates < 1 or request.max_turns < 1:
            raise AdapterError("candidates and max_turns must be positive")
        self.profile = get_profile(request.agent)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.store = RevisionStore(
            self.repository,
            self.output_dir / "state",
            protected_paths=(self.evaluator, self.dev_data, *request.protected_paths),
        )
        self.client = client or OpenAICompatibleClient(
            base_url=request.base_url,
            model=request.model,
            proxy=request.proxy,
        )

    def run(self) -> dict[str, object]:
        started = time.monotonic()
        deadline = started + self.request.timeout_seconds
        baseline = self.store.initialize()
        baseline_workspace = self.store.checkout(baseline, "baseline-evaluation")
        baseline_result = self.evaluate(baseline_workspace)
        best_revision = baseline
        best_score = baseline_result.score
        records: list[CandidateRecord] = []

        for candidate_index in range(1, self.request.candidates + 1):
            record = CandidateRecord(index=candidate_index)
            records.append(record)
            if time.monotonic() >= deadline:
                record.status = "timeout"
                break
            workspace = self.store.checkout(best_revision, f"candidate-{candidate_index:03d}")
            sandbox = RepositorySandbox(
                workspace,
                command_timeout_seconds=self.request.command_timeout_seconds,
            )
            try:
                self._run_candidate(
                    sandbox,
                    record,
                    candidate_index=candidate_index,
                    best_score=best_score,
                    deadline=deadline,
                )
                evaluation = self.evaluate(workspace)
                record.score = evaluation.score
                record.evaluations.append(evaluation.score)
                record.revision = self.store.commit(workspace, f"candidate {candidate_index}")
                record.status = "completed"
                if record.score > best_score:
                    best_score = record.score
                    best_revision = record.revision
            except Exception as exc:
                record.status = "failed"
                record.error = f"{type(exc).__name__}: {exc}"
            self._write_records(records, baseline, best_revision, best_score)

        if self.request.apply_best and best_revision != baseline:
            self.store.materialize(best_revision, self.repository, baseline)
        result = {
            "agent": self.request.agent,
            "profile": self.profile.display_name,
            "implementation": "shared-openai-repository-profile",
            "native_upstream_backend": False,
            "baseline_revision": baseline,
            "baseline_score": baseline_result.score,
            "best_revision": best_revision,
            "best_score": best_score,
            "improved": best_score > baseline_result.score,
            "applied": self.request.apply_best and best_revision != baseline,
            "changed_files": self.store.changed_files(best_revision, baseline),
            "duration_seconds": round(time.monotonic() - started, 3),
            "candidates": [asdict(record) for record in records],
        }
        (self.output_dir / "result.json").write_text(
            json.dumps(result, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return result

    def _run_candidate(
        self,
        sandbox: RepositorySandbox,
        record: CandidateRecord,
        *,
        candidate_index: int,
        best_score: float,
        deadline: float,
    ) -> None:
        messages: list[dict] = [
            {"role": "system", "content": self.profile.system_prompt},
            {
                "role": "user",
                "content": (
                    self.request.instruction
                    + "\n\n"
                    + self.profile.prompt_for(candidate_index, best_score)
                ),
            },
        ]
        submitted = False
        for turn in range(1, self.request.max_turns + 1):
            if time.monotonic() >= deadline:
                raise AdapterError("repository Agent total timeout reached")
            record.turns = turn
            response = self.client.complete(messages, TOOLS).message
            assistant_message = {
                "role": "assistant",
                "content": response.get("content"),
            }
            tool_calls = response.get("tool_calls") or []
            if tool_calls:
                assistant_message["tool_calls"] = tool_calls
            messages.append(assistant_message)
            if not tool_calls:
                messages.append(
                    {
                        "role": "user",
                        "content": "Use the available tools to inspect or modify the repository, then submit_candidate.",
                    }
                )
                continue
            for call in tool_calls:
                name = call["function"]["name"]
                try:
                    arguments = json.loads(call["function"].get("arguments") or "{}")
                    output, should_submit = self._execute_tool(name, arguments, sandbox, record)
                except Exception as exc:
                    output = f"{type(exc).__name__}: {exc}"
                    should_submit = False
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "content": output[-30000:],
                    }
                )
                submitted = submitted or should_submit
                if should_submit:
                    break
            if submitted:
                return
        raise AdapterError("candidate did not call submit_candidate before max_turns")

    def _execute_tool(
        self,
        name: str,
        arguments: dict,
        sandbox: RepositorySandbox,
        record: CandidateRecord,
    ) -> tuple[str, bool]:
        if name == "list_files":
            return sandbox.list_files(arguments.get("pattern", "*")), False
        if name == "read_file":
            return sandbox.read_text(
                arguments["path"],
                offset=int(arguments.get("offset", 1)),
                limit=int(arguments.get("limit", 400)),
            ), False
        if name == "write_file":
            return sandbox.write_text(arguments["path"], arguments["content"]), False
        if name == "replace_in_file":
            return sandbox.replace_text(
                arguments["path"], arguments["old"], arguments["new"]
            ), False
        if name == "run_shell":
            result = sandbox.run_shell(
                arguments["command"],
                timeout_seconds=arguments.get("timeout_seconds"),
            )
            return f"exit_code={result.return_code}\n{result.output}", False
        if name == "evaluate_dev":
            result = self.evaluate(sandbox.workspace)
            record.evaluations.append(result.score)
            return (
                f"dev_score={result.score:.8f}\nexit_code={result.return_code}\n{result.output}",
                False,
            )
        if name == "submit_candidate":
            return "candidate submitted", True
        raise AdapterError(f"unknown repository tool: {name}")

    def evaluate(self, workspace: Path) -> EvaluationResult:
        with tempfile.TemporaryDirectory(prefix="evaluation-", dir=self.output_dir) as temporary:
            evaluation_workspace = Path(temporary) / "workspace"
            shutil.copytree(
                workspace,
                evaluation_workspace,
                symlinks=True,
                ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"),
            )
            sandbox = RepositorySandbox(
                evaluation_workspace,
                command_timeout_seconds=self.request.command_timeout_seconds,
            )
            completed = sandbox.run_evaluator(
                python_executable=self.request.python_executable,
                evaluator=self.evaluator,
                dev_data=self.dev_data,
                concurrency=self.request.evaluator_concurrency,
            )
        if completed.return_code:
            raise AdapterError(
                f"development evaluator exited {completed.return_code}: {completed.output[-4000:]}"
            )
        return EvaluationResult(
            score=parse_pass_rate(completed.output),
            return_code=completed.return_code,
            output=completed.output,
        )

    def _write_records(
        self,
        records: list[CandidateRecord],
        baseline: str,
        best_revision: str,
        best_score: float,
    ) -> None:
        payload = {
            "baseline_revision": baseline,
            "best_revision": best_revision,
            "best_score": best_score,
            "candidates": [asdict(record) for record in records],
        }
        (self.output_dir / "progress.json").write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )


def parse_pass_rate(output: str) -> float:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if not lines:
        raise AdapterError("could not parse a pass rate from empty evaluator output")
    final_line = lines[-1]
    ratio = re.fullmatch(
        r"(?:(?:passed|pass[_ -]?rate|reward)\s*[:=]\s*)?(\d+)\s*/\s*(\d+)",
        final_line,
        flags=re.IGNORECASE,
    )
    if ratio:
        passed_text, total_text = ratio.groups()
        passed = int(passed_text)
        total = int(total_text)
        if total > 0 and passed <= total:
            return passed / total
    reward = re.fullmatch(
        r"(?:reward|pass(?:ed)?[_ -]?rate)\s*[:=]\s*([01](?:\.\d+)?)",
        final_line,
        flags=re.IGNORECASE,
    )
    if reward:
        return float(reward.group(1))
    raise AdapterError(
        "development evaluator final line must be passed/total or pass_rate: <0..1>"
    )


__all__ = ["RepositoryAgentBackend", "RepositoryAgentRequest", "parse_pass_rate"]
