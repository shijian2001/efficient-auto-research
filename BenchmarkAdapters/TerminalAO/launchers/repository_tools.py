"""Repository candidate operations shared by native search-loop backends.

Scope note: ``candidate_prompt`` was once shared by the MLEvolve and ML-Master 2.0
Terminal AO launchers. Both are now fail-closed stubs -- those Agents are excluded from
the AO comparison set (see ``registry.TERMINAL_AO_UNSUPPORTED_REASONS``) -- so this
module's sole remaining consumer is ``launchers/ear.py``, which is itself migrating to a
native EAR loop. A benchmark-authored prompt is scaffolding, not Agent behavior: do not
add new consumers here to make another Agent appear to run Terminal AO. If the EAR
migration lands, ``candidate_prompt`` and its helpers become dead and should be deleted.
"""

from __future__ import annotations

import hashlib
import json
import re
import shlex
import subprocess
from pathlib import Path
from typing import Any


ALLOWED_PATHS = (
    "terminus_2.py",
    "terminus_json_plain_parser.py",
    "terminus_xml_plain_parser.py",
    "tmux_session.py",
    "templates/",
)


def extract_unified_diff(response: str) -> str:
    fenced = re.search(r"```(?:diff|patch)?\s*\n(.*?)```", response, re.DOTALL)
    candidate = fenced.group(1) if fenced else response
    start = candidate.find("--- ")
    if start < 0:
        raise RuntimeError("model response did not contain a unified diff")
    return candidate[start:].strip() + "\n"


def validate_diff_paths(diff_text: str) -> tuple[str, ...]:
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
        if not any(relative == item or (item.endswith("/") and relative.startswith(item)) for item in ALLOWED_PATHS):
            raise RuntimeError(f"diff changes a non-editable path: {relative}")
        paths.add(relative)
    if not paths:
        raise RuntimeError("diff contains no editable file paths")
    return tuple(sorted(paths))


def git(*args: str, workspace: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        ["git", *args], cwd=workspace, capture_output=True, text=True, check=False
    )
    if check and completed.returncode:
        raise RuntimeError(f"git {' '.join(args)} failed: {completed.stderr.strip()}")
    return completed


def apply_candidate_diff(workspace: Path, parent_commit: str, diff_text: str) -> str:
    validate_diff_paths(diff_text)
    git("reset", "--hard", parent_commit, workspace=workspace)
    git("clean", "-fd", workspace=workspace)
    patch_path = workspace / ".terminal-ao-candidate.patch"
    patch_path.write_text(diff_text, encoding="utf-8")
    try:
        git("apply", "--check", str(patch_path), workspace=workspace)
        git("apply", str(patch_path), workspace=workspace)
    finally:
        patch_path.unlink(missing_ok=True)
    git("add", "--", *ALLOWED_PATHS, workspace=workspace)
    git("commit", "-q", "-m", "Terminal AO candidate", workspace=workspace)
    return git("rev-parse", "HEAD", workspace=workspace).stdout.strip()


def evaluate_dev(workspace: Path, dev_command: str) -> dict[str, Any]:
    completed = subprocess.run(
        shlex.split(dev_command), cwd=workspace, capture_output=True, text=True, check=False
    )
    if completed.returncode:
        raise RuntimeError(f"dev evaluation failed: {(completed.stderr or completed.stdout)[-2000:]}")
    payload = json.loads(completed.stdout)
    required = {"candidate_id", "candidate_digest", "pass_rate", "expected_tasks"}
    if not required.issubset(payload):
        raise RuntimeError("dev evaluation response is missing required fields")
    if int(payload["expected_tasks"]) != 36:
        raise RuntimeError("dev evaluation denominator is not the frozen 36 tasks")
    return payload


def commit_for_head(workspace: Path) -> str:
    return git("rev-parse", "HEAD", workspace=workspace).stdout.strip()


def embedding_for_text(text: str):
    import numpy as np

    values = np.frombuffer(hashlib.sha256(text.encode("utf-8")).digest(), dtype=np.uint8).astype(float)
    norm = np.linalg.norm(values)
    return values / norm if norm else values


def candidate_prompt(
    *,
    strategy: str,
    parent_diff: str | None,
    parent_feedback: dict[str, Any] | None,
    history: list[dict[str, Any]],
) -> tuple[str, str]:
    system = (
        "You optimize the Harbor terminus-2 coding-agent harness. Return exactly one unified diff "
        "against the current repository. You may edit only terminus_2.py, "
        "terminus_json_plain_parser.py, terminus_xml_plain_parser.py, tmux_session.py, or templates/. "
        "Do not modify tests, install dependencies, create symlinks, or seek held-out task data."
    )
    user = (
        f"Search strategy: {strategy}. Improve general Terminal-Bench task-solving reliability using "
        "only aggregate DEV feedback. Analyze parsing, shell/tool execution, prompt discipline, context "
        "management, and recovery.\n\n"
        f"Parent aggregate feedback: {json.dumps(parent_feedback or {}, sort_keys=True)}\n"
        f"Recent search outcomes: {json.dumps(history[-8:], sort_keys=True)}\n"
        f"Parent diff, if any:\n{parent_diff or '(frozen baseline)'}\n\n"
        "Output only a complete unified diff applicable with git apply."
    )
    return system, user


__all__ = [
    "ALLOWED_PATHS",
    "apply_candidate_diff",
    "candidate_prompt",
    "commit_for_head",
    "embedding_for_text",
    "evaluate_dev",
    "extract_unified_diff",
    "git",
    "validate_diff_paths",
]
