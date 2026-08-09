"""Host-owned development evaluation broker for one Autoresearch outer run."""

from __future__ import annotations

import json
import secrets
import socketserver
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from ..contracts import AdapterError
from .evaluator import CandidateEvaluation, CandidateEvaluator
from .revisions import TrainRevision, TrainRevisionStore


@dataclass(frozen=True)
class ScoredCandidate:
    revision: TrainRevision
    evaluation: CandidateEvaluation
    evaluation_sequence: int = 0
    completed_elapsed_seconds: float = 0.0


class CandidateDevBroker:
    """The only candidate creation and dev-score capability exposed to agents."""

    def __init__(
        self,
        *,
        revision_store: TrainRevisionStore,
        evaluator: CandidateEvaluator,
        dev_seed: int,
        output_dir: Path,
        agent: str | None = None,
        outer_seed: int | None = None,
    ) -> None:
        self.revision_store = revision_store
        self.evaluator = evaluator
        self.dev_seed = dev_seed
        self.output_dir = output_dir.resolve()
        self.output_dir.mkdir(parents=True, exist_ok=False)
        self.agent = agent
        self.outer_seed = outer_seed
        self._lock = threading.Lock()
        self._candidate_count = 0
        self._evaluation_count = 0
        self._scored: list[ScoredCandidate] = []
        self._evaluated_revision_ids: set[str] = set()
        self._declared_revision_id: str | None = None
        self._started_monotonic = time.monotonic()

    @property
    def calls(self) -> tuple[ScoredCandidate, ...]:
        return tuple(self._scored)

    @property
    def best(self) -> ScoredCandidate | None:
        valid = [item for item in self._scored if item.evaluation.score_valid]
        if not valid:
            return None
        return min(valid, key=lambda item: (float(item.evaluation.val_bpb), self._scored.index(item)))

    @property
    def declared_revision_id(self) -> str | None:
        return self._declared_revision_id

    def create_candidate(self, train_source: str, *, parent_id: str = "baseline") -> TrainRevision:
        with self._lock:
            self._candidate_count += 1
            revision_id = f"candidate-{self._candidate_count:04d}"
            return self.revision_store.commit_train_source(
                train_source,
                parent_id=parent_id,
                revision_id=revision_id,
                creation_metadata={
                    "agent": self.agent,
                    "outer_seed": self.outer_seed,
                    "candidate_sequence": self._candidate_count,
                },
            )

    def evaluate(self, revision_id: str) -> ScoredCandidate:
        with self._lock:
            revision = self.revision_store.get(revision_id)
            self._evaluation_count += 1
            evaluation_id = f"dev-{self._evaluation_count:04d}"
            evaluation = self.evaluator.evaluate(
                store=self.revision_store,
                revision_id=revision_id,
                seed=self.dev_seed,
                output_dir=self.output_dir / evaluation_id,
                evaluation_id=evaluation_id,
                agent=self.agent,
                outer_seed=self.outer_seed,
                candidate_sequence=self._evaluation_count,
            )
            scored = ScoredCandidate(
                revision,
                evaluation,
                evaluation_sequence=self._evaluation_count,
                completed_elapsed_seconds=time.monotonic() - self._started_monotonic,
            )
            self._scored.append(scored)
            self._evaluated_revision_ids.add(revision_id)
            return scored

    def declare_final(self, revision_id: str) -> TrainRevision:
        revision = self.revision_store.get(revision_id)
        if revision_id not in self._evaluated_revision_ids:
            raise AdapterError("Autoresearch final revision must have dev feedback")
        self._declared_revision_id = revision_id
        return revision

    @staticmethod
    def feedback(scored: ScoredCandidate) -> dict[str, object]:
        evaluation = scored.evaluation
        return {
            "revision_id": scored.revision.revision_id,
            "candidate_sha256": scored.revision.train_sha256,
            "status": evaluation.status.value,
            "score_valid": evaluation.score_valid,
            "val_bpb": evaluation.val_bpb,
            "metrics": dict(evaluation.metrics),
            "failure_reason": evaluation.failure_reason,
            "metric_direction": "minimize",
            "split": "development",
        }


class DevBrokerServer:
    """Authenticated local socket exposing no held-out evaluation capability."""

    def __init__(self, broker: CandidateDevBroker, socket_path: Path) -> None:
        self.broker = broker
        self.socket_path = socket_path.resolve()
        self.token = secrets.token_hex(32)
        self._server: socketserver.UnixStreamServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._server is not None:
            raise AdapterError("Autoresearch dev broker is already running")
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        self.socket_path.parent.chmod(0o700)
        owner = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self) -> None:
                try:
                    request = json.loads(self.rfile.readline().decode("utf-8"))
                    if request.get("token") != owner.token:
                        raise AdapterError("invalid Autoresearch dev broker capability token")
                    operation = request.get("operation")
                    if operation == "create-candidate":
                        revision = owner.broker.create_candidate(
                            str(request["train_source"]),
                            parent_id=str(request.get("parent_revision_id", "baseline")),
                        )
                        payload: object = {
                            "revision_id": revision.revision_id,
                            "candidate_sha256": revision.train_sha256,
                        }
                    elif operation == "evaluate-dev":
                        payload = owner.broker.feedback(
                            owner.broker.evaluate(str(request["revision_id"]))
                        )
                    elif operation == "declare-final":
                        revision = owner.broker.declare_final(str(request["revision_id"]))
                        payload = {
                            "revision_id": revision.revision_id,
                            "candidate_sha256": revision.train_sha256,
                        }
                    elif operation == "best-dev":
                        best = owner.broker.best
                        payload = owner.broker.feedback(best) if best else None
                    else:
                        raise AdapterError("unsupported Autoresearch dev broker operation")
                    response = {"ok": True, "result": payload}
                except Exception as exc:
                    response = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
                self.wfile.write(json.dumps(response, sort_keys=True).encode("utf-8") + b"\n")

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

    def __enter__(self) -> "DevBrokerServer":
        self.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.stop()


__all__ = ["CandidateDevBroker", "DevBrokerServer", "ScoredCandidate"]
