"""
Task-domain interface and the domain-agnostic Kernel Thompson Sampling driver.

EAR's contribution is the search strategy in `agent.engine.thompson` (Kernel
Thompson Sampling over a similarity-kernelled attempt tree). That strategy is
independent of what a "candidate" is and of where its score comes from: it only
needs a graph of attempts carrying a scalar metric.

This module separates the two concerns:

  * `TaskDomain` — what a candidate IS and how it gets scored. One
    implementation per task shape.
  * `run_kts_search` — the loop skeleton: budget check, KTS parent selection,
    delegate the step to the domain, record the attempt, track the running best
    and the stagnation counter that heats KTS exploration.

The MLE domain (a candidate is a Python script, the metric is parsed from the
script's own stdout) is implemented inline by `GraphSearchEngine.run()` in
`agent.engine.search`. That path is deliberately left untouched: it is the
scored MLE-Bench code and its behaviour must stay bit-identical. It matches this
interface in shape, so adopting the driver there is a later, separately
validated change.

The repository domain (a candidate is a git diff, the metric comes from an
injected external evaluator) is implemented by
`agent.engine.repo_domain.RepoDomain` and driven by `run_kts_search`.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from agent.engine.graph import Attempt, SearchGraph
from agent.engine.thompson import select_parent

logger = logging.getLogger("AutoResearch")


class TaskDomain:
    """
    What a candidate is, and how it is produced and scored.

    Subclasses own everything task-specific; the driver owns nothing but the
    search skeleton. The contract is intentionally tiny — a domain that can
    turn a parent node into a scored `Attempt` is all Kernel Thompson Sampling
    needs.

    `metric_sign` is +1 when a HIGHER metric is better and -1 when a LOWER
    metric is better. It is injected rather than inferred: the caller of a
    domain always knows the direction of its own objective, and every
    "is this better?" comparison in the driver routes through it.
    """

    metric_sign: int = 1

    def step(self, parent: Attempt | None, step_index: int) -> Attempt | None:
        """Produce and score one candidate derived from `parent`.

        Return the resulting `Attempt` (with `metric` set on success, or
        `error` set on failure — a failed attempt is still useful evidence and
        is kept in the graph), or None to skip the step entirely without
        recording a node.
        """
        raise NotImplementedError

    def on_new_best(self, attempt: Attempt) -> None:
        """Called when `attempt` becomes the best-so-far. Persist artifacts here."""

    def on_step_recorded(self, record: dict[str, Any]) -> None:
        """Called after each recorded step, for incremental observability."""

    def token_usage(self) -> tuple[int, int]:
        """Cumulative (input_tokens, output_tokens) spent by this domain."""
        return 0, 0


@dataclass
class SearchSummary:
    """Outcome of one KTS search run."""

    best_attempt: Attempt | None = None
    best_metric: float | None = None
    steps_taken: int = 0
    elapsed_seconds: float = 0.0
    stopped_reason: str = "max_steps"
    step_log: list[dict[str, Any]] = field(default_factory=list)


def is_better(metric: float | None, reference: float | None, metric_sign: int) -> bool:
    """True if `metric` beats `reference` under the domain's optimization
    direction. A missing metric never wins; any real metric beats a missing
    reference."""
    if metric is None:
        return False
    if reference is None:
        return True
    return metric_sign * metric > metric_sign * reference


def run_kts_search(
    *,
    domain: TaskDomain,
    graph: SearchGraph,
    max_steps: int,
    time_limit: float,
    start_time: float | None = None,
) -> SearchSummary:
    """
    Run the Kernel Thompson Sampling search loop over an arbitrary domain.

    Per step: check the budget, let KTS pick a parent (its exploration variance
    heated by the current stagnation count), hand the parent to the domain,
    record the returned attempt, and update the best/stagnation state.

    The search strategy itself is untouched — `select_parent` is called exactly
    as the MLE path calls it, with the same (stagnation, metric_sign) inputs.
    """
    started = start_time if start_time is not None else time.time()
    summary = SearchSummary()
    best_metric: float | None = None
    best_attempt: Attempt | None = None
    stagnation = 0

    for step in range(max_steps):
        elapsed = time.time() - started
        if elapsed > time_limit:
            logger.info(f"Time limit reached at step {step} ({elapsed:.0f}s)")
            summary.stopped_reason = "time_limit"
            break

        parent_id = select_parent(graph, stagnation, domain.metric_sign)
        parent = graph.attempts.get(parent_id) if parent_id else None
        logger.info(
            f"[Step {step}] parent={parent_id}, best={best_metric}, stagnation={stagnation}"
        )

        attempt = domain.step(parent, step)
        if attempt is None:
            # The domain declined to produce a node (e.g. the model returned
            # nothing usable). Count it against stagnation so the search keeps
            # widening rather than silently retrying the same basin.
            stagnation += 1
            continue

        graph.add_attempt(attempt)
        summary.steps_taken += 1

        if is_better(attempt.metric, best_metric, domain.metric_sign):
            best_metric = attempt.metric
            best_attempt = attempt
            domain.on_new_best(attempt)
            logger.info(f"  New best: {best_metric:.4f}")
            stagnation = 0
        else:
            stagnation += 1

        in_tokens, out_tokens = domain.token_usage()
        record = {
            "step": step,
            "parent_id": parent_id,
            "attempt_id": attempt.id,
            "metric": attempt.metric,
            "error": attempt.error,
            "best_so_far": best_metric,
            "cumulative_tokens": in_tokens + out_tokens,
            "elapsed_seconds": time.time() - started,
        }
        summary.step_log.append(record)
        domain.on_step_recorded(record)

    summary.best_attempt = best_attempt
    summary.best_metric = best_metric
    summary.elapsed_seconds = time.time() - started
    return summary


__all__ = ["SearchSummary", "TaskDomain", "is_better", "run_kts_search"]
