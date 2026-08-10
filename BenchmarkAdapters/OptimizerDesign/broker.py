"""Development-only capability broker for Optimizer Design."""

from __future__ import annotations

import json
import secrets
import socketserver
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from ..contracts import AdapterError
from .evaluator import OptimizerDesignEvaluation, OptimizerDesignEvaluator
from .revisions import OptimizerRevision, OptimizerRevisionStore


@dataclass(frozen=True)
class ScoredOptimizerCandidate:
    revision: OptimizerRevision
    evaluation: OptimizerDesignEvaluation
    evaluation_sequence: int
    completed_elapsed_seconds: float


class OptimizerDesignDevBroker:
    def __init__(
        self,
        *,
        revision_store: OptimizerRevisionStore,
        evaluator: OptimizerDesignEvaluator,
        development_seed: int,
        output_dir: Path,
        agent: str,
        outer_seed: int,
        outer_deadline_monotonic: float,
    ) -> None:
        self.revision_store = revision_store
        self.evaluator = evaluator
        self.development_seed = development_seed
        self.output_dir = output_dir.resolve()
        self.output_dir.mkdir(parents=True, exist_ok=False)
        self.agent = agent
        self.outer_seed = outer_seed
        self.outer_deadline_monotonic = outer_deadline_monotonic
        self._lock = threading.Lock()
        self._candidate_count = 0
        self._evaluation_count = 0
        self._scored: list[ScoredOptimizerCandidate] = []
        self._evaluated: set[str] = set()
        self._declared: str | None = None
        self._started = time.monotonic()

    @property
    def calls(self) -> tuple[ScoredOptimizerCandidate, ...]:
        return tuple(self._scored)

    @property
    def best(self) -> ScoredOptimizerCandidate | None:
        valid = [
            item
            for item in self._scored
            if item.evaluation.score_valid and item.evaluation.score_steps is not None
        ]
        return min(
            valid,
            key=lambda item: (
                int(item.evaluation.score_steps),
                float(item.evaluation.val_loss or float("inf")),
                item.evaluation_sequence,
            ),
            default=None,
        )

    @property
    def declared_revision_id(self) -> str | None:
        return self._declared

    def create_candidate(self, source: str, *, parent_id: str = "baseline") -> OptimizerRevision:
        with self._lock:
            self._candidate_count += 1
            return self.revision_store.commit_source(
                source,
                parent_id=parent_id,
                revision_id=f"candidate-{self._candidate_count:04d}",
                creation_metadata={
                    "agent": self.agent,
                    "outer_seed": self.outer_seed,
                    "candidate_sequence": self._candidate_count,
                },
            )

    def evaluate(self, revision_id: str) -> ScoredOptimizerCandidate:
        with self._lock:
            remaining = self.outer_deadline_monotonic - time.monotonic()
            if remaining <= 0:
                raise AdapterError("Optimizer Design outer search budget is exhausted")
            revision = self.revision_store.get(revision_id)
            self._evaluation_count += 1
            evaluation_id = f"dev-{self._evaluation_count:04d}"
            evaluation = self.evaluator.evaluate(
                revision.path,
                seed=self.development_seed,
                output_dir=self.output_dir / evaluation_id,
                evaluation_id=evaluation_id,
                timeout_seconds=min(self.evaluator.timeout_seconds, max(0.001, remaining)),
            )
            scored = ScoredOptimizerCandidate(
                revision=revision,
                evaluation=evaluation,
                evaluation_sequence=self._evaluation_count,
                completed_elapsed_seconds=time.monotonic() - self._started,
            )
            self._scored.append(scored)
            self._evaluated.add(revision_id)
            return scored

    def declare_final(self, revision_id: str) -> OptimizerRevision:
        revision = self.revision_store.get(revision_id)
        if revision_id not in self._evaluated:
            raise AdapterError("Optimizer Design final revision must have development feedback")
        self._declared = revision_id
        return revision

    def scored(self, revision_id: str) -> ScoredOptimizerCandidate | None:
        return next(
            (item for item in self._scored if item.revision.revision_id == revision_id),
            None,
        )

    @staticmethod
    def feedback(scored: ScoredOptimizerCandidate) -> dict[str, object]:
        evaluation = scored.evaluation
        return {
            "revision_id": scored.revision.revision_id,
            "candidate_sha256": scored.revision.candidate_sha256,
            "status": evaluation.status,
            "score_valid": evaluation.score_valid,
            "score_steps": evaluation.score_steps,
            "val_loss": evaluation.val_loss,
            "target_reached": evaluation.target_reached,
            "metrics": asdict(evaluation),
            "failure_reason": evaluation.failure_reason,
            "metric_direction": "minimize",
            "split": "development",
        }


class OptimizerDesignBrokerServer:
    def __init__(self, broker: OptimizerDesignDevBroker, socket_path: Path) -> None:
        self.broker = broker
        self.socket_path = socket_path.resolve()
        self.token = secrets.token_hex(32)
        self._server: socketserver.UnixStreamServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._server is not None:
            raise AdapterError("Optimizer Design broker is already running")
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        owner = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self) -> None:
                try:
                    request = json.loads(self.rfile.readline().decode("utf-8"))
                    if request.get("token") != owner.token:
                        raise AdapterError("invalid Optimizer Design broker token")
                    operation = request.get("operation")
                    if operation == "create-candidate":
                        revision = owner.broker.create_candidate(
                            str(request["train_source"]),
                            parent_id=str(request.get("parent_revision_id", "baseline")),
                        )
                        payload: object = {
                            "revision_id": revision.revision_id,
                            "candidate_sha256": revision.candidate_sha256,
                        }
                    elif operation == "evaluate-dev":
                        payload = owner.broker.feedback(
                            owner.broker.evaluate(str(request["revision_id"]))
                        )
                    elif operation == "declare-final":
                        revision = owner.broker.declare_final(str(request["revision_id"]))
                        payload = {
                            "revision_id": revision.revision_id,
                            "candidate_sha256": revision.candidate_sha256,
                        }
                    elif operation == "best-dev":
                        best = owner.broker.best
                        payload = owner.broker.feedback(best) if best else None
                    else:
                        raise AdapterError("unsupported Optimizer Design broker operation")
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

    def __enter__(self) -> "OptimizerDesignBrokerServer":
        self.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.stop()


__all__ = [
    "OptimizerDesignBrokerServer",
    "OptimizerDesignDevBroker",
    "ScoredOptimizerCandidate",
]
