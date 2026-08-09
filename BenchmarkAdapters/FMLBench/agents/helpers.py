"""Generic repository operations used by native FML search loops."""

from __future__ import annotations

import hashlib
import json
import re
import shlex
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any


def git(*arguments: str, workspace: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        ["git", *arguments], cwd=workspace, capture_output=True, text=True, check=False
    )
    if check and completed.returncode:
        raise RuntimeError(f"git {' '.join(arguments)} failed: {completed.stderr[-2000:]}")
    return completed


def initialize_repository(workspace: Path) -> str:
    if not (workspace / ".git").is_dir():
        git("init", "-q", workspace=workspace)
        git("config", "user.name", "FML Shared Adapter", workspace=workspace)
        git("config", "user.email", "fml-adapter@example.invalid", workspace=workspace)
        git("add", ".", workspace=workspace)
        git("commit", "-q", "-m", "Frozen FML baseline", workspace=workspace)
    return git("rev-parse", "HEAD", workspace=workspace).stdout.strip()


def extract_unified_diff(response: str) -> str:
    fenced = re.search(r"```(?:diff|patch)?\s*\n(.*?)```", response, re.DOTALL)
    candidate = fenced.group(1) if fenced else response
    start = candidate.find("--- ")
    if start < 0:
        raise RuntimeError("model response did not contain a unified diff")
    return candidate[start:].strip() + "\n"


def validate_diff_paths(diff_text: str, editable_paths: tuple[str, ...]) -> tuple[str, ...]:
    allowed = tuple(PurePosixPath(value) for value in editable_paths)
    paths: set[str] = set()
    for line in diff_text.splitlines():
        if not line.startswith(("--- ", "+++ ")):
            continue
        raw = line[4:].split("\t", 1)[0].strip()
        if raw == "/dev/null":
            continue
        relative = raw.removeprefix("a/").removeprefix("b/")
        path = PurePosixPath(relative)
        if path.is_absolute() or ".." in path.parts:
            raise RuntimeError(f"diff contains unsafe path: {raw}")
        if not any(path == item or item in path.parents for item in allowed):
            raise RuntimeError(f"diff changes non-editable path: {relative}")
        paths.add(relative)
    if not paths:
        raise RuntimeError("diff contains no editable paths")
    return tuple(sorted(paths))


def apply_candidate_diff(
    workspace: Path, parent_commit: str, diff_text: str, editable_paths: tuple[str, ...]
) -> str:
    validate_diff_paths(diff_text, editable_paths)
    git("reset", "--hard", parent_commit, workspace=workspace)
    git("clean", "-fd", workspace=workspace)
    patch = workspace / ".git/fml-candidate.patch"
    patch.write_text(diff_text, encoding="utf-8")
    try:
        git("apply", "--check", str(patch), workspace=workspace)
        git("apply", str(patch), workspace=workspace)
    finally:
        patch.unlink(missing_ok=True)
    git("add", "--", *editable_paths, workspace=workspace)
    git("commit", "-q", "-m", "FML candidate", workspace=workspace)
    return git("rev-parse", "HEAD", workspace=workspace).stdout.strip()


def evaluate_development(workspace: Path, command: str) -> dict[str, Any]:
    completed = subprocess.run(
        shlex.split(command), cwd=workspace, capture_output=True, text=True, check=False
    )
    if completed.returncode:
        raise RuntimeError(f"FML development evaluation failed: {(completed.stderr or completed.stdout)[-2000:]}")
    payload = json.loads(completed.stdout)
    if payload.get("status") != "completed" or not isinstance(payload.get("metric"), (int, float)):
        raise RuntimeError(f"FML development evaluation was invalid: {payload}")
    return payload


def model_settings() -> tuple[dict[str, Any], int | None, dict[str, Any]]:
    parameters = json.loads(__import__("os").environ["FML_MODEL_PARAMETERS"])
    timeout_raw = __import__("os").environ.get("FML_REQUEST_TIMEOUT_SECONDS", "")
    retry = json.loads(__import__("os").environ.get("FML_RETRY_POLICY", "{}"))
    return parameters, int(timeout_raw) if timeout_raw else None, retry


def query_openai(system: str, user: str, *, model: str) -> tuple[str, dict[str, int]]:
    from openai import OpenAI

    parameters, timeout, retry = model_settings()
    client = OpenAI(
        api_key=__import__("os").environ.get("OPENAI_API_KEY"),
        base_url=__import__("os").environ["OPENAI_BASE_URL"],
        timeout=timeout,
        max_retries=int(retry.get("max_retries", max(0, int(retry.get("max_attempts", 1)) - 1))),
    )
    kwargs = {
        name: value
        for name, value in parameters.items()
        if name in {"temperature", "reasoning_effort", "max_tokens", "max_output_tokens"}
    }
    if "max_output_tokens" in kwargs:
        kwargs["max_tokens"] = kwargs.pop("max_output_tokens")
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        **kwargs,
    )
    usage = getattr(response, "usage", None)
    return str(response.choices[0].message.content or ""), {
        "input_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
        "output_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
    }


def prompt_for_diff(
    *,
    task_input: str,
    strategy: str,
    editable_paths: tuple[str, ...],
    parent_diff: str | None,
    parent_feedback: dict[str, Any] | None,
    history: list[dict[str, Any]],
) -> tuple[str, str]:
    system = (
        "You are operating the registered research Agent's native search loop. Return exactly one "
        "unified diff. Modify only the explicitly editable paths. Do not install dependencies, read "
        "hidden evaluator assets, or change benchmark semantics."
    )
    user = (
        task_input
        + "\nSearch strategy supplied by the native Agent: "
        + strategy
        + "\nEditable paths: "
        + json.dumps(editable_paths)
        + "\nParent development feedback: "
        + json.dumps(parent_feedback or {}, sort_keys=True)
        + "\nRecent outcomes: "
        + json.dumps(history[-8:], sort_keys=True)
        + "\nParent diff:\n"
        + (parent_diff or "(frozen baseline)")
        + "\nOutput only an applicable unified diff."
    )
    return system, user


def embedding(text: str):
    import numpy as np

    values = np.frombuffer(hashlib.sha256(text.encode("utf-8")).digest(), dtype=np.uint8).astype(float)
    norm = np.linalg.norm(values)
    return values / norm if norm else values


__all__ = [
    "apply_candidate_diff",
    "embedding",
    "evaluate_development",
    "extract_unified_diff",
    "git",
    "initialize_repository",
    "model_settings",
    "prompt_for_diff",
    "query_openai",
    "validate_diff_paths",
]
