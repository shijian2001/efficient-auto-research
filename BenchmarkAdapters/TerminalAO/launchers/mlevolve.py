"""Terminal AO is not applicable to MLEvolve.

MLEvolve's engine decides whether a candidate node succeeded by testing for a
produced ``submission.csv`` (baselines/MLEvolve/engine/execution.py:26-30), and
manages its best solution by that same path
(baselines/MLEvolve/engine/solution_manager.py:71,169;
baselines/MLEvolve/agents/debug_agent.py:78). Terminal AO candidates are git
revisions of a frozen terminus-2 harness scored by an aggregate dev pass rate and
never produce such a file, so no adaptation exists that leaves the engine intact.

This module is a deliberate fail-closed stub. The previous implementation here was
benchmark-owned scaffolding -- an outer loop, prompt, diff extraction, evaluation and
selection all written by this adapter -- wrapped around a single MLEvolve selector (``engine.node_selection.select_with_soft_switch``). Reporting its output
as a MLEvolve Terminal AO score would attribute the harness's behavior to the Agent, so
the entry is removed rather than footnoted.

MLEvolve remains fully native on MLE-Bench Lite; nothing here affects that path.
"""

from __future__ import annotations

from ...contracts import UnsupportedAdapterError
from ...registry import TERMINAL_AO_UNSUPPORTED_REASONS


AGENT = "mlevolve"

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
