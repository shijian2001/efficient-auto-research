"""Strict source-edit utilities shared by external FML-bench adapters."""
from __future__ import annotations

import hashlib
import json
import re
import shlex
import subprocess
from pathlib import Path
from typing import Iterable, Tuple


def build_source_edit_prompt(
    base_prompt: str,
    repo_dir: str,
    target_files: Iterable[str],
    *,
    max_chars: int = 300_000,
) -> str:
    """Append complete target-file contents and a strict diff contract."""
    repo = Path(repo_dir).resolve()
    sections = []
    total = len(base_prompt)
    for target in target_files:
        path = Path(target).resolve()
        try:
            relative = path.relative_to(repo).as_posix()
        except ValueError as exc:
            raise ValueError(f"Target file is outside the repository: {path}") from exc
        if not path.is_file():
            raise FileNotFoundError(f"Target file does not exist: {path}")
        content = path.read_text(encoding="utf-8")
        section = (
            f"\n===== BEGIN TARGET FILE: {relative} =====\n"
            f"{content}"
            f"\n===== END TARGET FILE: {relative} =====\n"
        )
        total += len(section)
        if total > max_chars:
            raise ValueError(
                f"Target source context exceeds {max_chars} characters; "
                "refuse to truncate source code for an unsafe edit."
            )
        sections.append(section)

    return (
        base_prompt
        + "\n\n## Strict source-edit response contract\n"
        + "- Read the current target-file contents below before proposing an edit.\n"
        + "- Return exactly one focused hypothesis and one unified git diff.\n"
        + "- The diff may modify existing declared target files only.\n"
        + "- Do not create, delete, or rename files.\n"
        + "- Do not return a complete standalone training/submission script unless "
        "that script is itself one of the declared target files.\n"
        + "- Do not use SEARCH/REPLACE blocks or full-file prose responses.\n"
        + "- Use this exact shape:\n\n"
        + "HYPOTHESIS: <one concise sentence>\n"
        + "PATCH:\n"
        + "```diff\n"
        + "diff --git a/path/to/target.py b/path/to/target.py\n"
        + "--- a/path/to/target.py\n"
        + "+++ b/path/to/target.py\n"
        + "@@ ... @@\n"
        + "...\n"
        + "```\n"
        + "".join(sections)
    )


def apply_strict_target_patch(
    response_text: str,
    repo_dir: str,
    target_files: Iterable[str],
) -> Tuple[bool, str, str]:
    """Extract, validate, and apply a unified diff limited to target files."""
    hypothesis, patch = extract_hypothesis_and_patch(response_text)
    if not patch:
        return False, "No unified diff was returned.", hypothesis
    if "new file mode" in patch or "deleted file mode" in patch or "rename from" in patch:
        return False, "Creating, deleting, or renaming files is forbidden.", hypothesis

    repo = Path(repo_dir).resolve()
    allowed = {
        Path(path).resolve().relative_to(repo).as_posix()
        for path in target_files
    }
    touched = _diff_paths(patch)
    if not touched:
        return False, "The response did not contain valid diff --git headers.", hypothesis
    actual_paths, numstat_error = _git_apply_paths(repo, patch)
    if numstat_error:
        return False, numstat_error, hypothesis
    if actual_paths != touched:
        return (
            False,
            "Patch headers do not match the files git would modify.",
            hypothesis,
        )
    outside = sorted(touched - allowed)
    if outside:
        return (
            False,
            "Patch touches non-target files: " + ", ".join(outside),
            hypothesis,
        )
    missing = sorted(path for path in touched if not (repo / path).is_file())
    if missing:
        return False, "Patch target does not exist: " + ", ".join(missing), hypothesis

    before = _hash_files(repo, touched)
    check = subprocess.run(
        ["git", "apply", "--check", "--whitespace=nowarn", "-"],
        cwd=str(repo),
        input=patch,
        text=True,
        capture_output=True,
        check=False,
    )
    if check.returncode != 0:
        return False, check.stderr.strip() or "git apply --check failed.", hypothesis
    applied = subprocess.run(
        ["git", "apply", "--whitespace=nowarn", "-"],
        cwd=str(repo),
        input=patch,
        text=True,
        capture_output=True,
        check=False,
    )
    if applied.returncode != 0:
        return False, applied.stderr.strip() or "git apply failed.", hypothesis
    after = _hash_files(repo, touched)
    changed = sorted(path for path in touched if before[path] != after[path])
    if not changed:
        return False, "Patch applied without changing a target file.", hypothesis
    return True, "Applied target patch to: " + ", ".join(changed), hypothesis


def extract_hypothesis_and_patch(response_text: str) -> Tuple[str, str]:
    text = (response_text or "").strip()
    if not text:
        return "", ""

    payload = _extract_json_payload(text)
    if payload:
        hypothesis = str(payload.get("hypothesis", "")).strip()
        patch = str(payload.get("patch", "")).strip()
        if patch:
            return hypothesis, _normalize_patch(patch)

    hypothesis_match = re.search(r"(?im)^\s*HYPOTHESIS\s*:\s*(.+?)\s*$", text)
    hypothesis = hypothesis_match.group(1).strip() if hypothesis_match else ""
    fenced = re.search(r"```diff\s*\n(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        return hypothesis, _normalize_patch(fenced.group(1))
    start = text.find("diff --git ")
    return hypothesis, _normalize_patch(text[start:]) if start >= 0 else ""


def _extract_json_payload(text: str) -> dict:
    candidates = [text]
    fenced = re.search(r"```json\s*\n(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        candidates.insert(0, fenced.group(1))
    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return {}


def _normalize_patch(patch: str) -> str:
    normalized = patch.strip().replace("\r\n", "\n")
    return normalized + "\n" if normalized else ""


def _diff_paths(patch: str) -> set[str]:
    paths = set()
    for line in patch.splitlines():
        if not line.startswith("diff --git "):
            continue
        try:
            parts = shlex.split(line)
        except ValueError:
            return set()
        if len(parts) != 4 or not parts[2].startswith("a/") or not parts[3].startswith("b/"):
            return set()
        old_path = parts[2][2:]
        new_path = parts[3][2:]
        if old_path != new_path:
            return set()
        paths.add(new_path)
    return paths


def _git_apply_paths(repo: Path, patch: str) -> tuple[set[str], str]:
    result = subprocess.run(
        ["git", "apply", "--numstat", "-"],
        cwd=str(repo),
        input=patch,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return set(), result.stderr.strip() or "git apply --numstat failed."
    paths = set()
    for line in result.stdout.splitlines():
        columns = line.split("\t")
        if len(columns) < 3:
            return set(), "Could not determine every path modified by the patch."
        paths.add(columns[-1])
    return paths, ""


def _hash_files(repo: Path, relative_paths: Iterable[str]) -> dict[str, str]:
    return {
        relative: hashlib.sha256((repo / relative).read_bytes()).hexdigest()
        for relative in relative_paths
    }
