"""
Repository task domain: a candidate is a git diff, scored by an external evaluator.

This is the second `TaskDomain` implementation (the first being the MLE script
domain inlined in `agent.engine.search`). It lets EAR run its native Kernel
Thompson Sampling search on tasks whose unit of progress is a source-code
change rather than a modelling script:

  * a candidate is a unified diff restricted to an allowlist of editable paths,
    committed on top of its parent's commit, so the attempt tree in the graph
    and the commit tree in the repository stay in lock-step;
  * the metric comes from an evaluator that is INJECTED by the caller. The
    domain never learns what the evaluator is or how it computes its score —
    it hands over a workspace and receives a float plus opaque feedback. That
    keeps EAR free of any benchmark-harness knowledge.

Nothing here touches `agent.engine.thompson`: parent selection, the kernel and
the GP posterior are used exactly as the MLE path uses them.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import subprocess
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import numpy as np

from agent.engine.domain import TaskDomain
from agent.engine.graph import Attempt
from agent.llm import query as llm_query

logger = logging.getLogger("AutoResearch")


@dataclass
class EvaluationResult:
    """One score returned by the injected evaluator.

    `score` is the only thing the search consumes. `feedback` is an opaque
    mapping echoed back into the next prompt so the model can see what its
    parent achieved — the domain never interprets its contents.
    """

    score: float
    feedback: Mapping[str, Any] = field(default_factory=dict)


# The evaluator contract: given the workspace in its current (candidate) state,
# return a score. Anything the caller needs to reach a real evaluator — a
# socket, a subprocess, a stub in a test — is closed over by the caller.
Evaluator = Callable[[Path], EvaluationResult]


@dataclass
class RepoTaskConfig:
    """Everything the repository domain needs, all injected by the caller."""

    workspace: Path
    task_description: str
    editable_paths: tuple[str, ...]
    evaluator: Evaluator
    model: str = "gpt-5.5"
    metric_sign: int = 1
    # Bytes of editable-file source shown to the model. A unified diff must
    # quote exact context lines, so the model cannot write one against files it
    # has not seen; the whole editable surface is normally well within a modern
    # context window.
    context_budget_bytes: int = 400_000
    # Retries for one step when the model returns no usable diff.
    diff_retries: int = 3
    temperature: float | None = None
    # Recent attempts summarized into each prompt.
    history_window: int = 8


def git(*args: str, workspace: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        ["git", *args], cwd=str(workspace), capture_output=True, text=True, check=False
    )
    if check and completed.returncode:
        raise RuntimeError(f"git {' '.join(args)} failed: {completed.stderr.strip()}")
    return completed


def extract_unified_diff(response: str) -> str:
    """Pull a unified diff out of a model response, fenced or bare."""
    fenced = re.search(r"```(?:diff|patch)?\s*\n(.*?)```", response, re.DOTALL)
    candidate = fenced.group(1) if fenced else response
    start = candidate.find("--- ")
    if start < 0:
        start = candidate.find("diff --git ")
    if start < 0:
        raise RuntimeError("model response did not contain a unified diff")
    return candidate[start:].strip() + "\n"


def validate_diff_paths(
    diff_text: str, editable_paths: Sequence[str]
) -> tuple[str, ...]:
    """Reject any diff that escapes the allowlist before it is applied.

    This is a hard constraint of the task, not a heuristic: a candidate that
    edits a non-declared path is invalid regardless of how well it might score.
    """
    paths: set[str] = set()
    for line in diff_text.splitlines():
        if not line.startswith(("--- ", "+++ ")):
            continue
        raw = line[4:].split("\t", 1)[0].strip()
        if raw == "/dev/null":
            continue
        relative = raw.removeprefix("a/").removeprefix("b/")
        if relative.startswith("/") or ".." in Path(relative).parts:
            raise RuntimeError(f"diff contains an unsafe path: {raw}")
        allowed = any(
            relative == item
            or relative.startswith(item.rstrip("/") + "/")
            for item in editable_paths
        )
        if not allowed:
            raise RuntimeError(f"diff changes a non-editable path: {relative}")
        paths.add(relative)
    if not paths:
        raise RuntimeError("diff contains no editable file paths")
    return tuple(sorted(paths))


def _hash_embedding(text: str) -> np.ndarray:
    """Deterministic fallback embedding when the sentence encoder is absent."""
    values = np.frombuffer(
        hashlib.sha256(text.encode("utf-8")).digest(), dtype=np.uint8
    ).astype(float)
    norm = np.linalg.norm(values)
    return values / norm if norm else values


def embed_diff(diff_text: str, plan: str = "") -> np.ndarray:
    """Embed a candidate for the search kernel.

    Prefers EAR's native sentence encoder (the same one the MLE path uses, so
    kernel similarity means the same thing in both domains) and degrades to a
    deterministic hash embedding if the encoder is unavailable — a degraded
    kernel still yields a valid PSD Gram matrix, so the GP posterior stays
    well-defined.
    """
    try:
        from agent.engine.embedder import embed_attempt

        return embed_attempt(plan, diff_text, None, None)
    except Exception as exc:  # pragma: no cover - depends on model availability
        logger.warning(f"[repo] sentence embedder unavailable ({exc}); using hash embedding")
        return _hash_embedding(plan + "\n" + diff_text)


class RepoDomain(TaskDomain):
    """Candidate = unified diff on an allowlist; metric = injected evaluator score."""

    def __init__(self, config: RepoTaskConfig) -> None:
        self.config = config
        self.metric_sign = config.metric_sign
        self.workspace = Path(config.workspace)
        self.baseline_commit = git(
            "rev-parse", "HEAD", workspace=self.workspace
        ).stdout.strip()
        # Per-attempt bookkeeping: the commit each candidate lives on, its diff,
        # and the opaque feedback its evaluation returned.
        self.commits: dict[str, str] = {}
        self.diffs: dict[str, str] = {}
        self.feedback: dict[str, Mapping[str, Any]] = {}
        self.history: list[dict[str, Any]] = []
        self.total_in_tokens = 0
        self.total_out_tokens = 0
        self.best_commit: str | None = None
        self._source_view: str | None = None

    # --- TaskDomain interface ---

    def token_usage(self) -> tuple[int, int]:
        return self.total_in_tokens, self.total_out_tokens

    def on_new_best(self, attempt: Attempt) -> None:
        commit = self.commits.get(attempt.id)
        if commit:
            self.best_commit = commit

    def step(self, parent: Attempt | None, step_index: int) -> Attempt | None:
        attempt_id = f"repo-{step_index:04d}-{uuid.uuid4().hex[:6]}"
        parent_commit = (
            self.commits.get(parent.id, self.baseline_commit) if parent else self.baseline_commit
        )
        plan = "refine-parent-candidate" if parent is not None else "new-root-candidate"

        diff_text, error = self._generate_diff(parent)
        if error is not None:
            # No usable diff: record the failure as a node so the kernel and the
            # GP posterior learn that this region of the space is unproductive,
            # and keep it in the history so the run stays diagnosable.
            self.history.append(
                {
                    "id": attempt_id,
                    "parent_id": parent.id if parent else None,
                    "error": error,
                }
            )
            return Attempt(
                id=attempt_id,
                plan=plan,
                code="",
                error=error,
                parent_id=parent.id if parent else None,
                embedding=embed_diff("", plan),
            )

        try:
            commit = self._apply_candidate(parent_commit, diff_text)
            evaluation = self.config.evaluator(self.workspace)
            metric = float(evaluation.score)
        except Exception as exc:
            # Roll the workspace back to the parent so the next step starts from
            # a known-good tree rather than a half-applied candidate.
            git("reset", "--hard", parent_commit, workspace=self.workspace, check=False)
            git("clean", "-fd", workspace=self.workspace, check=False)
            failure = f"{type(exc).__name__}: {exc}"
            self.history.append(
                {
                    "id": attempt_id,
                    "parent_id": parent.id if parent else None,
                    "error": failure,
                }
            )
            return Attempt(
                id=attempt_id,
                plan=plan,
                code=diff_text,
                error=failure,
                parent_id=parent.id if parent else None,
                embedding=embed_diff(diff_text, plan),
            )

        self.commits[attempt_id] = commit
        self.diffs[attempt_id] = diff_text
        self.feedback[attempt_id] = dict(evaluation.feedback)
        self.history.append(
            {
                "id": attempt_id,
                "parent_id": parent.id if parent else None,
                "score": metric,
            }
        )
        return Attempt(
            id=attempt_id,
            plan=plan,
            code=diff_text,
            metric=metric,
            parent_id=parent.id if parent else None,
            embedding=embed_diff(diff_text, plan),
        )

    # --- candidate generation ---

    def _generate_diff(self, parent: Attempt | None) -> tuple[str, str | None]:
        """Query the model until it returns a diff that parses and stays in bounds."""
        system = self._system_prompt()
        user = self._user_prompt(parent)
        last_error = "no response"
        for retry in range(max(1, self.config.diff_retries)):
            kwargs: dict[str, Any] = {}
            if self.config.temperature is not None:
                kwargs["temperature"] = float(self.config.temperature)
            try:
                text, in_tokens, out_tokens = llm_query(
                    system, user, model=self.config.model, **kwargs
                )
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                logger.warning(f"[repo] LLM query failed: {last_error}")
                continue
            self.total_in_tokens += in_tokens
            self.total_out_tokens += out_tokens
            try:
                diff_text = extract_unified_diff(text)
                validate_diff_paths(diff_text, self.config.editable_paths)
                return diff_text, None
            except RuntimeError as exc:
                last_error = str(exc)
                logger.info(f"[repo] rejected candidate (retry {retry}): {last_error}")
                user = (
                    self._user_prompt(parent)
                    + f"\n\nYour previous response was rejected: {last_error}. "
                    "Return exactly one complete unified diff that applies with "
                    "`git apply` and touches only the editable paths listed above."
                )
        return "", f"CandidateFormatError: {last_error}"

    def _apply_candidate(self, parent_commit: str, diff_text: str) -> str:
        """Apply the diff on top of the parent commit and commit the result."""
        git("reset", "--hard", parent_commit, workspace=self.workspace)
        git("clean", "-fd", workspace=self.workspace)
        patch_path = self.workspace / ".ear-candidate.patch"
        patch_path.write_text(diff_text, encoding="utf-8")
        try:
            git("apply", "--check", str(patch_path), workspace=self.workspace)
            git("apply", str(patch_path), workspace=self.workspace)
        finally:
            patch_path.unlink(missing_ok=True)
        git("add", "--", *self.config.editable_paths, workspace=self.workspace)
        git("commit", "-q", "-m", "EAR repo candidate", workspace=self.workspace)
        return git("rev-parse", "HEAD", workspace=self.workspace).stdout.strip()

    def restore_best(self) -> str | None:
        """Leave the workspace on the best-scoring candidate (or the baseline)."""
        target = self.best_commit or self.baseline_commit
        git("reset", "--hard", target, workspace=self.workspace)
        git("clean", "-fd", workspace=self.workspace, check=False)
        return target

    # --- prompts ---

    def _system_prompt(self) -> str:
        """Role and output contract only.

        Deliberately free of methodology: it states what to produce and which
        paths may change, and says nothing about where to look for wins or what
        kinds of change tend to help. Any such hint would be a task-specific
        advantage this agent gets and a general-purpose coding agent solving the
        same task from the same task description does not.
        """
        editable = ", ".join(self.config.editable_paths)
        return (
            "You are a software engineer improving an existing codebase. "
            "You will be shown the task, the current source of the files you may "
            "change, and the score the current version achieved.\n\n"
            "Respond with exactly ONE unified diff and nothing else. The diff must:\n"
            f"- change only these paths: {editable}\n"
            "- apply cleanly with `git apply` against the source shown below\n"
            "- use correct unified-diff syntax with accurate line context\n"
            "Do not include commentary, explanations, or multiple alternative diffs."
        )

    def _user_prompt(self, parent: Attempt | None) -> str:
        """Task, constraints, current source, and the search's own history.

        Everything here is either given by the task description, mechanically
        derived from the repository, or produced by this run's own attempts.
        No domain analysis and no suggested directions are added.
        """
        parts = [f"## Task\n{self.config.task_description.strip()}"]

        parts.append(
            "## Constraints\n"
            f"- Editable paths: {', '.join(self.config.editable_paths)}\n"
            "- Every other path in the repository is frozen and must not appear in the diff.\n"
            "- Feedback is a single aggregate score; no per-case results are available."
        )

        parts.append("## Current source of the editable files\n" + self._editable_source_view())

        if parent is None:
            roots = [item for item in self.history if item.get("parent_id") is None]
            if roots:
                summary = json.dumps(roots[-self.config.history_window :], sort_keys=True)
                parts.append(
                    "## Independent candidates already tried\n"
                    f"{summary}\n"
                    "You are starting a new candidate from the unmodified baseline. "
                    "Make a change that is substantively different from the ones above."
                )
            else:
                parts.append(
                    "## Status\nNo candidate has been tried yet. "
                    "You are starting from the unmodified baseline."
                )
        else:
            parent_score = (
                f"{parent.metric:.4f}" if parent.metric is not None else "not scored"
            )
            parts.append(
                "## Candidate you are building on\n"
                f"Score: {parent_score}\n"
                f"Evaluator feedback: "
                f"{json.dumps(dict(self.feedback.get(parent.id, {})), sort_keys=True)}\n\n"
                "Its diff against the baseline:\n"
                f"```diff\n{self.diffs.get(parent.id, '(none)')}\n```\n\n"
                "The source shown above already includes this diff. Produce a further "
                "change on top of it, expressed as a diff against that current source."
            )
            if parent.error:
                parts.append(f"## Previous failure\n{parent.error}")

        if self.history:
            parts.append(
                "## Scores of recent candidates\n"
                + json.dumps(self.history[-self.config.history_window :], sort_keys=True)
            )

        parts.append("Output only the unified diff.")
        return "\n\n".join(parts)

    def _editable_source_view(self) -> str:
        """Current contents of every editable file, within the context budget.

        Rebuilt each step: the working tree changes as candidates are committed,
        and a diff written against stale source will not apply.
        """
        chunks: list[str] = []
        remaining = self.config.context_budget_bytes
        for relative in self.config.editable_paths:
            path = self.workspace / relative
            files = sorted(p for p in path.rglob("*") if p.is_file()) if path.is_dir() else [path]
            for file_path in files:
                if not file_path.is_file():
                    continue
                name = file_path.relative_to(self.workspace).as_posix()
                try:
                    text = file_path.read_text(encoding="utf-8", errors="replace")
                except OSError as exc:
                    chunks.append(f"### {name}\n(unreadable: {exc})")
                    continue
                if remaining <= 0:
                    chunks.append(f"### {name}\n(omitted: context budget exhausted)")
                    continue
                if len(text) > remaining:
                    text = text[:remaining] + "\n... (truncated: context budget)\n"
                remaining -= len(text)
                chunks.append(f"### {name}\n```\n{text}\n```")
        return "\n\n".join(chunks) if chunks else "(no editable files found)"


__all__ = [
    "EvaluationResult",
    "Evaluator",
    "RepoDomain",
    "RepoTaskConfig",
    "embed_diff",
    "extract_unified_diff",
    "git",
    "validate_diff_paths",
]
