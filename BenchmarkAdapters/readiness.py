"""Evidence-backed, layered readiness records."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import IntEnum
from pathlib import Path
from typing import Any

from .contracts import AdapterError
from .protocol import write_json_exclusive


class ReadinessLevel(IntEnum):
    NOT_READY = 0
    SOURCE_READY = 1
    ENVIRONMENT_READY = 2
    COMMAND_READY = 3
    REAL_SMOKE_READY = 4
    FORMAL_PROTOCOL_READY = 5


@dataclass(frozen=True)
class ReadinessEvidence:
    agent: str
    mode: str
    level: ReadinessLevel
    evidence_path: str | None
    observed_at: str
    detail: str

    def validate(self) -> None:
        if not self.agent or not self.mode or not self.observed_at or not self.detail:
            raise AdapterError("readiness evidence fields must not be empty")
        if self.level >= ReadinessLevel.REAL_SMOKE_READY:
            if not self.evidence_path or not Path(self.evidence_path).is_file():
                raise AdapterError("smoke/formal readiness requires a durable evidence file")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["level"] = self.level.name.lower()
        payload["level_value"] = int(self.level)
        return payload

    def write(self, path: Path) -> None:
        self.validate()
        write_json_exclusive(path, self.to_dict())


__all__ = ["ReadinessEvidence", "ReadinessLevel"]
