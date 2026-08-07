"""Dev-only evaluation broker; no test capability exists during search."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..contracts import AdapterError
from .evaluator import EvaluationRecord
from .protocol import TerminalAOProtocol


@dataclass(frozen=True)
class DevEvaluationRequest:
    candidate_id: str
    candidate_digest: str


class DevEvaluationBroker:
    def __init__(
        self,
        protocol: TerminalAOProtocol,
        evaluate: Callable[[DevEvaluationRequest], EvaluationRecord],
    ) -> None:
        protocol.validate()
        self.protocol = protocol
        self._evaluate = evaluate
        self.calls: list[DevEvaluationRequest] = []

    def evaluate(self, request: DevEvaluationRequest) -> EvaluationRecord:
        if not request.candidate_id or len(request.candidate_digest) != 64:
            raise AdapterError("dev broker requires a candidate identity and digest")
        result = self._evaluate(request)
        if result.split != "dev" or result.protocol_digest != self.protocol.digest:
            raise AdapterError("dev broker received an invalid or non-dev evaluation record")
        if result.candidate_digest != request.candidate_digest:
            raise AdapterError("dev broker candidate digest mismatch")
        self.calls.append(request)
        return result


__all__ = ["DevEvaluationBroker", "DevEvaluationRequest"]
