"""Helpers for keeping generated research artifacts out of git commits."""

from __future__ import annotations

from fnmatch import fnmatch


DEFAULT_ARTIFACT_PATTERNS: tuple[str, ...] = (
    ".coordinator/**",
    ".arbor/**",
    "results/**",
    "models/**",
    "logs/**",
    "analysis/**",
    "runs/**",
    "cache/**",
    "submissions/**",
    ".pytest_cache/**",
    ".mypy_cache/**",
    ".ruff_cache/**",
    "__pycache__/**",
    "*.log",
    "*.tmp",
)

DEFAULT_KEEP_PATTERNS: tuple[str, ...] = (
    "submission.csv",
)


def normalize_git_path(path: str) -> str:
    """Normalize git path output to POSIX-style relative paths."""
    normalized = path.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.lstrip("/")


def is_git_artifact_path(
    path: str,
    *,
    artifact_patterns: tuple[str, ...] = DEFAULT_ARTIFACT_PATTERNS,
    keep_patterns: tuple[str, ...] = DEFAULT_KEEP_PATTERNS,
) -> bool:
    """Return True when a path is a generated artifact that should not be committed."""
    normalized = normalize_git_path(path)
    if not normalized:
        return False
    if any(fnmatch(normalized, pattern) for pattern in keep_patterns):
        return False
    return any(fnmatch(normalized, pattern) for pattern in artifact_patterns)


def filter_commit_paths(paths: list[str] | set[str] | tuple[str, ...]) -> tuple[list[str], list[str]]:
    """Split paths into commit-worthy files and generated artifacts."""
    commit_paths: list[str] = []
    artifact_paths: list[str] = []
    for path in sorted({normalize_git_path(p) for p in paths if p.strip()}):
        if is_git_artifact_path(path):
            artifact_paths.append(path)
        else:
            commit_paths.append(path)
    return commit_paths, artifact_paths