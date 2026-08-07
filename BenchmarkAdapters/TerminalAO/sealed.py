"""Atomic one-shot consumption gate for held-out Terminal AO tests."""

from __future__ import annotations

import json
from pathlib import Path

from ..contracts import AdapterError


class SealedTestGate:
    def __init__(self, state_path: Path) -> None:
        self.state_path = state_path.resolve()

    def consume(self, *, protocol_digest: str, harness_digest: str) -> None:
        if len(protocol_digest) != 64 or len(harness_digest) != 64:
            raise AdapterError("sealed test requires protocol and frozen harness digests")
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "test_consumed": True,
            "protocol_digest": protocol_digest,
            "harness_digest": harness_digest,
        }
        try:
            with self.state_path.open("x", encoding="utf-8") as handle:
                json.dump(payload, handle, sort_keys=True, indent=2)
                handle.write("\n")
        except FileExistsError as exc:
            raise AdapterError("sealed Terminal AO test has already been consumed") from exc


__all__ = ["SealedTestGate"]
