"""JSONL file logger — every event becomes one line in events.jsonl.

This is the durable structured log. Useful for:
- post-hoc analysis (jq queries, stats)
- debugging crashes (the last event tells you where it died)
- driving downstream report generation
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from ..bus import Event, EventBus

log = logging.getLogger(__name__)


class JsonlFileLogger:
    """Append every event to a JSONL file.

    Holds an open file handle for the lifetime of the session; close() must
    be called to flush. Safe to use as a `with` context manager.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fp = self.path.open("a", encoding="utf-8")

    def attach(self, bus: EventBus) -> None:
        bus.on_all(self._on_event)

    def _on_event(self, event: Event) -> None:
        record = {
            "ts": event.timestamp,
            "type": event.type,
            "data": event.data,
        }
        try:
            self._fp.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
            self._fp.flush()
        except Exception:
            # Never let logging blow up the run; record why for debugging.
            log.debug("failed to write event %s to %s", event.type, self.path, exc_info=True)

    def close(self) -> None:
        try:
            self._fp.close()
        except Exception:
            log.debug("failed to close %s", self.path, exc_info=True)

    def __enter__(self) -> "JsonlFileLogger":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()
