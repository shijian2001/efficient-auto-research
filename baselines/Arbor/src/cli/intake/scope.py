"""Intent and filesystem-scope policy for the interactive intake chat.

The intake agent is conversational, not an unattended code agent.  This module
keeps two control decisions out of the LLM prompt:

* whether the current conversation is discussion or launch planning;
* which canonical paths the user has actually authorized for file tools.

Both decisions are deliberately conservative.  Ambiguous requests keep their
current mode, and discussion mode has no implicit filesystem access.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class IntakeMode(str, Enum):
    """The two supported intake interaction modes."""

    DISCUSSION = "discussion"
    PLANNING = "planning"


_NEGATED_LAUNCH_RE = re.compile(
    r"(?:不要|无需|不需要|先不|暂不|别)(?:再)?(?:启动|运行|执行|跑)|"
    r"(?:不要|别)做实验|"
    r"\b(?:do\s+not|don't|dont|not\s+yet)\s+(?:launch|run|execute|start)\b",
    re.IGNORECASE,
)
_EXPLICIT_LAUNCH_RE = re.compile(
    r"(?:开始|启动|运行|执行|开跑)(?:吧|它|这个|一下|起来)?(?:$|[，。！？,.!?])|"
    r"(?:启动|开始|运行|执行|跑)(?:这个|一次|一轮|新的)?(?:实验|评测|研究|agent|项目)|"
    r"(?:提分|跑实验|开始实验)|"
    r"\b(?:launch|start|run|execute)\s+(?:it|this|now)\b|"
    r"\b(?:launch|start|run|execute)\s+(?:the\s+|an?\s+)?(?:experiment|benchmark|research\s+run|agent)\b|"
    r"\b(?:go ahead|kick (?:it|this) off)\b",
    re.IGNORECASE | re.DOTALL,
)
_PLANNING_RE = re.compile(
    r"(?:优化|提高|提升|最大化|最小化).{0,20}(?:指标|分数|得分|准确率|score|metric)|"
    r"\b(?:optimi[sz]e|maximi[sz]e|minimi[sz]e|improve|beat)\b.{0,40}"
    r"\b(?:score|metric|accuracy|benchmark|baseline)\b",
    re.IGNORECASE | re.DOTALL,
)
_DISCUSSION_RE = re.compile(
    r"(?:只|先|帮我)?(?:阅读|读一下|看看|分析|讨论|评估|评价|梳理|排查)|"
    r"(?:研究方向|论文方向|想法|思路|故事|新颖性|novelty|topic)|"
    r"\b(?:read|review|discuss|analy[sz]e|brainstorm|explain|compare)\b",
    re.IGNORECASE,
)

_LAUNCH_APPROVAL_RE = re.compile(
    r"(?:(?:yes|y|ok|okay|sounds good|approved?)"
    r"(?:\s*,?\s*(?:please|go ahead|proceed|start|launch|do it|kick it off))?|"
    r"go|go ahead|proceed|start|launch|do it|kick it off)|"
    r"(?:(?:好|好的|可以|同意|没问题)"
    r"(?:[，,\s]*(?:开始|启动|开跑|执行)(?:吧)?)?|"
    r"(?:确认[，,\s]*)?(?:开始|启动|开跑|执行)(?:吧)?|就这样)",
    re.IGNORECASE,
)


def infer_intake_mode(
    message: str,
    current: IntakeMode | None = None,
) -> IntakeMode:
    """Infer the interaction mode while respecting explicit negative intent.

    Launch wording wins over generic discussion wording, except when the user
    explicitly says not to launch or run anything.  With no signal, preserve
    the current mode; fresh sessions retain Arbor's historical planning default.
    """

    text = " ".join((message or "").split())
    if _NEGATED_LAUNCH_RE.search(text):
        return IntakeMode.DISCUSSION
    if _EXPLICIT_LAUNCH_RE.search(text):
        return IntakeMode.PLANNING
    if _DISCUSSION_RE.search(text):
        return IntakeMode.DISCUSSION
    if _PLANNING_RE.search(text):
        return IntakeMode.PLANNING
    return current or IntakeMode.PLANNING


def is_explicit_launch_approval(message: str) -> bool:
    """Return whether *message* unambiguously approves the staged launch.

    Approval is intentionally a whole-message decision.  A reply such as
    ``"yes, but use a different split"`` changes the plan and must be staged
    again instead of approving stale arguments.
    """

    text = " ".join((message or "").strip().split()).strip("。！!，,")
    return bool(_LAUNCH_APPROVAL_RE.fullmatch(text))


# Bare paths require at least one separator.  This intentionally ignores lone
# filenames in prose: restricting to a guessed file would be worse than asking
# the user for an exact path.  Backtick/quote extraction below still accepts a
# quoted lone filename when it exists.
_BARE_PATH_RE = re.compile(
    r"(?:~[/\\]|/|\.\.?[/\\]|[A-Za-z0-9_.~-]+[/\\])"
    r"[^\s`\"'<>|，。；;：:!?！？]+"
)
_QUOTED_PATH_RE = re.compile(r"`([^`\n]+)`|['\"]([^'\"\n]+)['\"]")
_TRAILING_PATH_PUNCTUATION = ".,;:!?，。；：！？)]}）】》"
_LEADING_PATH_PUNCTUATION = "([{（【《"


def extract_explicit_paths(message: str) -> list[str]:
    """Return path-like strings explicitly present in a user message."""

    found: list[str] = []

    def _add(raw: str, *, quoted: bool = False) -> None:
        candidate = raw.strip().lstrip(_LEADING_PATH_PUNCTUATION).rstrip(
            _TRAILING_PATH_PUNCTUATION
        )
        if not candidate or "://" in candidate or candidate.startswith("//"):
            return
        if "/" not in candidate and "\\" not in candidate:
            # A quoted lone filename is still unambiguous enough to resolve.
            if not Path(candidate).suffix:
                return
        elif not quoted and not _is_strong_path_candidate(candidate):
            # Slash-separated prose such as ``client/server`` is common. Bare
            # paths must carry a strong signal (absolute/dot-relative, Windows
            # separators, or a filename suffix); quote a relative directory to
            # authorize it deliberately.
            return
        if candidate not in found:
            found.append(candidate)

    for match in _QUOTED_PATH_RE.finditer(message or ""):
        _add(match.group(1) or match.group(2) or "", quoted=True)
    for match in _BARE_PATH_RE.finditer(message or ""):
        _add(match.group(0))
    return found


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


@dataclass
class IntakePathPolicy:
    """Mutable, session-level path authorization for intake file tools.

    Planning mode may inspect the confirmed starting project. Discussion mode
    may inspect only paths explicitly named by the user. Explicit paths replace
    the previous discussion scope, so a correction narrows access instead of
    silently accumulating old projects.
    """

    starting_cwd: Path
    mode: IntakeMode = IntakeMode.PLANNING
    explicit_paths: tuple[Path, ...] = ()
    explicit_directories: tuple[Path, ...] = ()
    unresolved_paths: tuple[str, ...] = ()
    target_confirmation_required: bool = False

    def __post_init__(self) -> None:
        self.starting_cwd = self.starting_cwd.expanduser().resolve()

    def reset(self, *, mode: IntakeMode = IntakeMode.PLANNING) -> None:
        self.mode = mode
        self.explicit_paths = ()
        self.explicit_directories = ()
        self.unresolved_paths = ()
        self.target_confirmation_required = False

    def update(self, message: str, mode: IntakeMode) -> None:
        """Apply a user turn to the current mode and approved scope."""

        mode_changed = mode != self.mode
        previous_paths = self.explicit_paths
        self.mode = mode
        candidates = extract_explicit_paths(message)
        if not candidates:
            # Scope does not flow implicitly between discussion and launch
            # planning.  The two modes grant different capabilities, so a
            # transition without a path fails closed until the user names the
            # target again (planning still has its explicit cwd default).
            if mode_changed:
                self.explicit_paths = ()
                self.explicit_directories = ()
                self.unresolved_paths = ()
                self.target_confirmation_required = bool(
                    mode == IntakeMode.PLANNING
                    and any(
                        not _is_within(path, self.starting_cwd)
                        for path in previous_paths
                    )
                )
            return

        resolved: list[Path] = []
        directories: list[Path] = []
        unresolved: list[str] = []
        for candidate in candidates:
            path = self._resolve_user_path(candidate)
            if path is None:
                unresolved.append(candidate)
            elif path not in resolved:
                resolved.append(path)
                if path.is_dir():
                    directories.append(path)

        self.explicit_paths = tuple(resolved)
        self.explicit_directories = tuple(directories)
        self.unresolved_paths = tuple(unresolved)
        self.target_confirmation_required = False

    def authorize(self, canonical_path: str) -> str | None:
        """Return a user-facing denial when *canonical_path* is out of scope."""

        path = Path(canonical_path)
        allowed = self._allowed_roots()
        if any(
            path == root
            or (
                (root == self.starting_cwd or root in self.explicit_directories)
                and _is_within(path, root)
            )
            for root in allowed
        ):
            return None

        if self.mode == IntakeMode.DISCUSSION:
            if self.unresolved_paths and not self.explicit_paths:
                detail = ", ".join(self.unresolved_paths)
                return (
                    "the path is outside the user's approved discussion scope; "
                    f"the explicitly named path(s) could not be resolved: {detail}"
                )
            return (
                "the path is outside the user's approved discussion scope. "
                "Only explicitly named files or directories may be inspected; "
                "ask the user before expanding scope"
            )
        roots = ", ".join(str(root) for root in allowed) or "none"
        return (
            f"the path is outside the approved project scope ({roots}). "
            "Ask the user to name the external path explicitly"
        )

    def describe(self) -> str:
        """Render the current authorization boundary for the system prompt."""

        allowed = self._allowed_roots()
        if allowed:
            lines = [f"- {path}" for path in allowed]
            scope = "\n".join(lines)
        else:
            scope = "- no filesystem paths are currently approved"
        unresolved = ""
        if self.unresolved_paths:
            unresolved = (
                "\nThe following user-supplied paths could not be resolved; ask for "
                "a corrected absolute path instead of searching parent directories: "
                + ", ".join(self.unresolved_paths)
            )
        if self.target_confirmation_required:
            unresolved += (
                "\nThe discussion concerned paths outside the launch project. "
                "Ask the user to name the intended project directory explicitly "
                "before inspecting files or staging a run."
            )
        return scope + unresolved

    def _allowed_roots(self) -> tuple[Path, ...]:
        if self.target_confirmation_required:
            return ()
        if self.mode == IntakeMode.DISCUSSION:
            return self.explicit_paths
        if self.unresolved_paths:
            return ()
        if self.explicit_paths:
            external = tuple(
                path
                for path in self.explicit_paths
                if (
                    not _is_within(path, self.starting_cwd)
                    and path in self.explicit_directories
                )
            )
            # An explicitly named external target redirects intake away from
            # the launch directory. Do not retain broad access to both trees.
            if external:
                return external
            external_files = tuple(
                path
                for path in self.explicit_paths
                if not _is_within(path, self.starting_cwd)
            )
            if external_files:
                return external_files
            return (self.starting_cwd,)
        return (self.starting_cwd,)

    def _resolve_user_path(self, raw: str) -> Path | None:
        normalized = raw.replace("\\", os.sep) if os.sep == "/" else raw
        expanded = Path(normalized).expanduser()
        if expanded.is_absolute():
            lexical_candidates = [expanded]
        else:
            # A common workspace layout has the CLI repo and user material as
            # siblings.  Resolve the exact, explicitly named path against the
            # launch directory and its immediate parent only.  Never glob or
            # search ancestors, and never climb more than this one documented
            # workspace-relative fallback.
            bases = [self.starting_cwd, self.starting_cwd.parent]
            lexical_candidates = [base / expanded for base in bases]

        for lexical in lexical_candidates:
            absolute = Path(os.path.abspath(lexical))
            if not absolute.exists():
                continue
            try:
                canonical = absolute.resolve()
            except OSError:
                continue
            # Do not let an in-project symlink turn implicit project permission
            # into access outside the project. An explicitly named absolute or
            # ancestor-relative external path remains allowed as itself.
            if _is_within(absolute, self.starting_cwd) and not _is_within(
                canonical, self.starting_cwd
            ):
                continue
            return canonical
        return None


def _is_strong_path_candidate(candidate: str) -> bool:
    """Distinguish likely paths from conceptual slash-separated prose."""

    normalized = candidate.replace("\\", "/")
    return (
        normalized.startswith(("/", "~/", "./", "../"))
        or "\\" in candidate
        or bool(Path(normalized).suffix)
    )
