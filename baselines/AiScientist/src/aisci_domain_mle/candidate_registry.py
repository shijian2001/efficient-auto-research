from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class CandidateSnapshot:
    source: Path
    snapshot: Path
    registry_path: Path


class CandidateRegistry:
    def __init__(self, registry_path: Path, candidates_dir: Path):
        self.registry_path = registry_path
        self.candidates_dir = candidates_dir
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        self.candidates_dir.mkdir(parents=True, exist_ok=True)
        if not self.registry_path.exists():
            self.registry_path.write_text("", encoding="utf-8")

    def append(self, event: str, **payload: Any) -> None:
        record = {"ts": _utcnow(), "event": event, **payload}
        with self.registry_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=True) + "\n")

    def snapshot_submission(
        self,
        submission_path: Path,
        *,
        reason: str,
        method_summary: str,
        metrics: dict[str, Any] | None = None,
        eval_protocol: str = "unknown",
    ) -> CandidateSnapshot:
        existing = sorted(self.candidates_dir.glob("candidate_*.csv"))
        next_index = len(existing) + 1
        snapshot_path = self.candidates_dir / f"candidate_{next_index:03d}.csv"
        shutil.copy2(submission_path, snapshot_path)
        self.append(
            "candidate_detail",
            candidate_path=str(snapshot_path),
            reason=reason,
            method_summary=method_summary,
            metrics=metrics,
            eval_protocol=eval_protocol,
            src=str(submission_path),
            dst=str(snapshot_path),
        )
        return CandidateSnapshot(
            source=submission_path,
            snapshot=snapshot_path,
            registry_path=self.registry_path,
        )

    def select_champion(
        self,
        champion_path: Path,
        *,
        rationale: str,
        metrics: dict[str, Any] | None = None,
        eval_protocol: str = "unknown",
    ) -> None:
        self.append(
            "champion_selected",
            champion_path=str(champion_path),
            rationale=rationale,
            metrics=metrics,
            eval_protocol=eval_protocol,
        )
