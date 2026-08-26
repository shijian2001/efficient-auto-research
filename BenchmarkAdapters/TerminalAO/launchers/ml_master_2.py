"""Terminal AO is not applicable to ML-Master 2.0.

ML-Master 2.0's playground hard-codes a Kaggle-shaped workspace
(best_submission/best_solution/submission/working) and promotes its best solution
by copying ``submission_<uid>.csv``
(baselines/EvoMaster/playground/ml_master_2/core/playground.py:107-113,212,300).
Terminal AO produces no such artifact, so its staged Draft/Research/Improve
workflow cannot express AO candidates without rewriting that core.

This module is a deliberate fail-closed stub. The previous implementation here was
benchmark-owned scaffolding -- an outer loop, prompt, diff extraction, evaluation and
selection all written by this adapter -- wrapped around a benchmark-authored three-stage prompt sequence over ``evomaster.agent``. Reporting its output
as a ML-Master 2.0 Terminal AO score would attribute the harness's behavior to the Agent, so
the entry is removed rather than footnoted.

ML-Master 2.0 remains fully native on MLE-Bench Lite; nothing here affects that path.
"""

from __future__ import annotations

from ...contracts import UnsupportedAdapterError
from ...registry import TERMINAL_AO_UNSUPPORTED_REASONS


AGENT = "ml-master-2"

UNSUPPORTED_REASON = TERMINAL_AO_UNSUPPORTED_REASONS[AGENT]


def run_native_loop(*args: object, **kwargs: object) -> dict[str, object]:
    raise UnsupportedAdapterError(
        f"{AGENT} does not participate in Terminal-Bench AO: {UNSUPPORTED_REASON}"
    )


def main(argv: list[str] | None = None) -> int:
    raise UnsupportedAdapterError(
        f"{AGENT} does not participate in Terminal-Bench AO: {UNSUPPORTED_REASON}"
    )


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["AGENT", "UNSUPPORTED_REASON", "main", "run_native_loop"]
