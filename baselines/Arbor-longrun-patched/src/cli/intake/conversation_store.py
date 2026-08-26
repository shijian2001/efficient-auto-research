"""Persist the pre-launch intake conversation so it can be resumed.

The intake REPL (``arbor`` with no subcommand) is a planning chat held *before*
a research run is launched. Until now that chat lived only in memory: quit
before launching and it was gone, so there was nothing to ``/resume``. Launched
runs persist under ``.arbor/sessions/<run_name>/`` (see
:mod:`arbor.coordinator.checkpoint`); this module is the analogous, deliberately
lighter store for the *conversation* itself.

Layout, one dir per conversation under the launch directory::

    <cwd>/.arbor/conversations/<conv_id>/
        messages.jsonl   # the agent's message history (atomic JSONL IO)
        meta.json        # id, timestamps, title, turn count, launched flag

A conversation is **unfinished** while ``launched`` is false. Once the chat
fires ``LaunchExperiment`` the run's own checkpoint takes over, so the record is
marked ``launched`` and excluded from ``--continue`` (it is kept as history).

Message IO is reused verbatim from the checkpoint contract
(:func:`arbor.coordinator.checkpoint.write_messages` /
:func:`~arbor.coordinator.checkpoint.read_messages`) so a conversation and a run
serialize their histories identically. Meta writes are atomic (temp + replace)
for the same reason checkpoints are: an interrupt must never leave a torn file.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..._app import CONFIG_DIR_NAME
from ...coordinator.checkpoint import read_messages, write_messages

#: Bump when the on-disk meta shape changes incompatibly.
SCHEMA_VERSION = 1

CONVERSATIONS_DIRNAME = "conversations"
MESSAGES_NAME = "messages.jsonl"
META_NAME = "meta.json"

_TITLE_MAX = 80
_CONV_ID_RE = re.compile(r"conv_\d{8}_\d{6}(?:_\d+)?")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def conversations_root(cwd: str | os.PathLike[str]) -> Path:
    """Return ``<cwd>/.arbor/conversations`` (the per-project conversation root)."""
    return Path(cwd) / CONFIG_DIR_NAME / CONVERSATIONS_DIRNAME


@dataclass
class ConversationRecord:
    """One saved intake conversation, addressed by ``conv_id`` under ``cwd``."""

    conv_id: str
    cwd: Path
    created_at: str
    updated_at: str
    title: str = ""
    turns: int = 0
    launched: bool = False

    @property
    def dir(self) -> Path:
        return conversations_root(self.cwd) / self.conv_id

    @property
    def messages_path(self) -> Path:
        return self.dir / MESSAGES_NAME

    @property
    def meta_path(self) -> Path:
        return self.dir / META_NAME

    def to_meta(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "conv_id": self.conv_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "title": self.title,
            "turns": self.turns,
            "launched": self.launched,
        }

    @classmethod
    def from_meta(cls, cwd: Path, data: dict[str, Any]) -> "ConversationRecord":
        return cls(
            conv_id=str(data["conv_id"]),
            cwd=Path(cwd),
            created_at=str(data.get("created_at") or ""),
            updated_at=str(data.get("updated_at") or data.get("created_at") or ""),
            title=str(data.get("title") or ""),
            turns=int(data.get("turns") or 0),
            launched=bool(data.get("launched", False)),
        )


def new_conversation(cwd: str | os.PathLike[str]) -> ConversationRecord:
    """Mint a fresh record (timestamp-derived id). Does NOT touch disk.

    The id is unique even within the same second: if a directory already
    exists, a numeric suffix is appended.
    """
    now = datetime.now()
    base = now.strftime("conv_%Y%m%d_%H%M%S")
    root = conversations_root(cwd)
    conv_id = base
    n = 1
    while (root / conv_id).exists():
        n += 1
        conv_id = f"{base}_{n}"
    iso = _utc_now_iso()
    return ConversationRecord(
        conv_id=conv_id, cwd=Path(cwd), created_at=iso, updated_at=iso
    )


def save_conversation(
    rec: ConversationRecord,
    messages: list[dict[str, Any]],
    *,
    launched: bool = False,
) -> None:
    """Persist ``messages`` + refreshed meta for ``rec`` atomically.

    Updates ``rec`` in place (``updated_at``, ``turns``, ``title``, ``launched``)
    so the caller's handle stays current across repeated saves.
    """
    if not _CONV_ID_RE.fullmatch(rec.conv_id):
        raise ValueError(f"invalid conversation id: {rec.conv_id!r}")
    root = conversations_root(rec.cwd)
    root.mkdir(parents=True, exist_ok=True)
    if root.is_symlink():
        raise OSError(f"refusing symlinked conversation root: {root}")
    try:
        root.resolve(strict=True).relative_to(rec.cwd.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise OSError(f"conversation root escapes project: {root}") from exc
    rec.dir.mkdir(parents=False, exist_ok=True)
    if rec.dir.is_symlink():
        raise OSError(f"refusing symlinked conversation directory: {rec.dir}")
    write_messages(rec.messages_path, _messages_for_disk(messages))

    rec.updated_at = _utc_now_iso()
    rec.turns = _count_user_turns(messages)
    rec.launched = launched
    if not rec.title:
        rec.title = _derive_title(messages)

    _atomic_write_json(rec.meta_path, rec.to_meta())


def load_messages(rec: ConversationRecord) -> list[dict[str, Any]]:
    """Load the saved message history (``[]`` if none), tolerant of corruption."""
    return read_messages(rec.messages_path)


def find_conversations(cwd: str | os.PathLike[str]) -> list[ConversationRecord]:
    """Return all readable conversations under ``cwd``, newest update first.

    Defensive: a dir without a parseable ``meta.json`` is skipped, never fatal.
    """
    root = conversations_root(cwd)
    if not root.is_dir() or root.is_symlink():
        return []
    try:
        resolved_root = root.resolve(strict=True)
        resolved_root.relative_to(Path(cwd).resolve(strict=True))
    except (OSError, ValueError):
        return []

    records: list[ConversationRecord] = []
    for conv_dir in root.iterdir():
        if (
            not conv_dir.is_dir()
            or conv_dir.is_symlink()
            or not _CONV_ID_RE.fullmatch(conv_dir.name)
        ):
            continue
        try:
            conv_dir.resolve(strict=True).relative_to(resolved_root)
        except (OSError, ValueError):
            continue
        data = _load_json(conv_dir / META_NAME)
        if (
            not isinstance(data, dict)
            or data.get("conv_id") != conv_dir.name
            or (conv_dir / META_NAME).is_symlink()
            or (conv_dir / MESSAGES_NAME).is_symlink()
        ):
            continue
        try:
            records.append(ConversationRecord.from_meta(Path(cwd), data))
        except (KeyError, ValueError, TypeError):
            continue

    records.sort(key=lambda r: r.updated_at, reverse=True)
    return records


def latest_unfinished(cwd: str | os.PathLike[str]) -> ConversationRecord | None:
    """Return the newest non-launched conversation with real content, or None."""
    for rec in find_conversations(cwd):
        if not rec.launched and rec.turns > 0 and rec.messages_path.is_file():
            return rec
    return None


# ── helpers ──────────────────────────────────────────────────────────


def _count_user_turns(messages: list[dict[str, Any]]) -> int:
    return sum(
        1
        for m in messages
        if m.get("role") == "user" and not m.get("_internal")
    )


def _derive_title(messages: list[dict[str, Any]]) -> str:
    """First user message, flattened to a short single line."""
    for m in messages:
        if m.get("role") != "user" or m.get("_internal"):
            continue
        text = _message_text(m.get("content"))
        text = " ".join(text.split())
        if text:
            return text if len(text) <= _TITLE_MAX else text[: _TITLE_MAX - 1] + "…"
    return ""


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and isinstance(b.get("text"), str)
        ]
        return " ".join(p for p in parts if p)
    return ""


def _messages_for_disk(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Copy history while removing file contents returned by intake tools.

    The live agent keeps full results in memory for the current conversation.
    Persisted chat is resumable context, not a second copy of every file the
    user authorized Arbor to inspect.
    """

    sanitized: list[dict[str, Any]] = []
    for message in messages:
        if message.get("_internal") == "context_summary":
            sanitized.append({
                "role": "user",
                "_internal": "context_summary",
                "content": (
                    "[compacted context omitted from persisted intake history; "
                    "restate the current goal and re-authorize any needed paths]"
                ),
            })
            continue
        content = message.get("content")
        if not isinstance(content, list):
            sanitized.append(dict(message))
            continue
        blocks: list[Any] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                blocks.append({
                    **block,
                    "content": (
                        "[tool result omitted from persisted intake history; "
                        "ask the user to re-authorize the path before re-reading]"
                    ),
                })
            else:
                blocks.append(block)
        sanitized.append({**message, "content": blocks})
    return sanitized


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2, ensure_ascii=False)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fp:
            fp.write(payload)
            fp.flush()
            os.fsync(fp.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _load_json(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
