"""Atomic one-shot consumption gate for held-out Terminal AO tests."""

from __future__ import annotations

from pathlib import Path

from ..contracts import AdapterError
from ..formal_contract import write_hashed_json


class SealedTestGate:
    def __init__(self, state_path: Path) -> None:
        self.state_path = state_path.resolve()

    def consume(
        self,
        *,
        protocol_digest: str,
        split_digest: str,
        harness_digest: str,
        outer_run_index: int,
    ) -> str:
        if any(len(value) != 64 for value in (protocol_digest, split_digest, harness_digest)):
            raise AdapterError("sealed test requires protocol and frozen harness digests")
        payload = {
            "schema_version": 2,
            "test_consumed": True,
            "protocol_digest": protocol_digest,
            "split_digest": split_digest,
            "harness_digest": harness_digest,
            "outer_run_index": outer_run_index,
        }
        try:
            return write_hashed_json(
                self.state_path,
                payload,
                digest_field="gate_digest",
            )
        except AdapterError as exc:
            if "overwrite" in str(exc):
                raise AdapterError("sealed Terminal AO test has already been consumed") from exc
            raise


__all__ = ["SealedTestGate"]
