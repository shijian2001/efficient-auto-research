"""Host-owned dev-only evaluation broker for one Terminal AO run."""

from __future__ import annotations

import json
import secrets
import socketserver
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping

from ..contracts import AdapterError
from .baseline import tree_digest
from .evaluator import EvaluationRecord, evaluate_harness
from .protocol import TerminalAOProtocol
from .revisions import Revision, RevisionStore


@dataclass(frozen=True)
class ScoredRevision:
    revision: Revision
    evaluation: EvaluationRecord


class CandidateDevBroker:
    def __init__(
        self,
        *,
        protocol: TerminalAOProtocol,
        revision_store: RevisionStore,
        candidate_dir: Path,
        output_dir: Path,
        socket_path: Path,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        self.protocol = protocol
        self.revision_store = revision_store
        self.candidate_dir = candidate_dir.resolve()
        self.output_dir = output_dir.resolve()
        self.socket_path = socket_path.resolve()
        self.environment = dict(environment or {})
        self.token = secrets.token_hex(32)
        self._lock = threading.Lock()
        self._calls = 0
        self._scored: list[ScoredRevision] = []
        self._by_digest: dict[str, ScoredRevision] = {}
        self._server: socketserver.UnixStreamServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def best(self) -> ScoredRevision | None:
        if not self._scored:
            return None
        return max(
            self._scored,
            key=lambda item: (
                item.evaluation.pass_rate,
                -item.evaluation.errors,
                -item.evaluation.missing_rewards,
                -self._scored.index(item),
            ),
        )

    @property
    def calls(self) -> tuple[ScoredRevision, ...]:
        return tuple(self._scored)

    def evaluate_current(self) -> ScoredRevision:
        with self._lock:
            current_digest = tree_digest(self.candidate_dir)
            existing = self._by_digest.get(current_digest)
            if existing is not None:
                return existing
            self._calls += 1
            revision_id = f"candidate-{self._calls:04d}"
            revision = self.revision_store.commit(
                self.candidate_dir,
                parent_id="baseline",
                revision_id=revision_id,
            )
            evaluation = evaluate_harness(
                self.protocol,
                split_name="dev",
                harness_dir=revision.path,
                evaluation_dir=self.output_dir / "dev-evaluations" / revision_id,
                environment=self.environment,
            )
            scored = ScoredRevision(revision, evaluation)
            self._scored.append(scored)
            self._by_digest[revision.tree_digest] = scored
            return scored

    def restricted_payload(self, scored: ScoredRevision) -> dict[str, object]:
        record = scored.evaluation
        return {
            "candidate_id": scored.revision.revision_id,
            "candidate_digest": scored.revision.tree_digest,
            "pass_rate": record.pass_rate,
            "passed": record.passed,
            "failed": record.failed,
            "errors": record.errors,
            "missing_rewards": record.missing_rewards,
            "expected_tasks": record.expected_tasks,
        }

    def start(self) -> None:
        if self._server is not None:
            raise AdapterError("dev broker is already running")
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        self.socket_path.parent.chmod(0o700)
        broker = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self) -> None:
                try:
                    request = json.loads(self.rfile.readline().decode("utf-8"))
                    if request.get("token") != broker.token:
                        raise AdapterError("invalid dev broker capability token")
                    if request.get("operation") != "evaluate-dev":
                        raise AdapterError("unsupported dev broker operation")
                    scored = broker.evaluate_current()
                    response = {"ok": True, "evaluation": broker.restricted_payload(scored)}
                except Exception as exc:
                    response = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
                self.wfile.write(json.dumps(response, sort_keys=True).encode() + b"\n")

        self._server = socketserver.UnixStreamServer(str(self.socket_path), Handler)
        self.socket_path.chmod(0o600)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=10)
        self.socket_path.unlink(missing_ok=True)
        self._server = None
        self._thread = None

    def __enter__(self) -> "CandidateDevBroker":
        self.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.stop()


__all__ = ["CandidateDevBroker", "ScoredRevision"]
