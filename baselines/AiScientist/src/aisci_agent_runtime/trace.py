from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class TracePaths:
    agent_log: Path
    conversation_jsonl: Path
    session_state: Path


def trace_paths(log_dir: Path) -> TracePaths:
    return TracePaths(
        agent_log=log_dir / "agent.log",
        conversation_jsonl=log_dir / "conversation.jsonl",
        session_state=log_dir / "session_state.json",
    )


class AgentTraceWriter:
    def __init__(self, log_dir: Path):
        self.paths = trace_paths(log_dir)
        self.paths.agent_log.parent.mkdir(parents=True, exist_ok=True)

    def log(self, message: str) -> None:
        with self.paths.agent_log.open("a", encoding="utf-8") as handle:
            handle.write(message.rstrip() + "\n")

    def event(
        self,
        event_type: str,
        message: str,
        *,
        phase: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        record = {
            "ts": _utcnow(),
            "event_type": event_type,
            "phase": phase,
            "message": message,
            "payload": payload or {},
        }
        with self.paths.conversation_jsonl.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=True) + "\n")
        self.log(f"[{event_type}] {message}")

    def write_state(self, **state: Any) -> None:
        payload = {"updated_at": _utcnow(), **state}
        self.paths.session_state.write_text(
            json.dumps(payload, indent=2, ensure_ascii=True),
            encoding="utf-8",
        )
