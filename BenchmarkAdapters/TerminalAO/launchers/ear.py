"""EAR Terminal AO launcher: hand the task to EAR's native repository mode.

This launcher does what the codex/claude launchers do — prepare the environment
and the task, start the Agent, wait for it to finish, collect the artifact. It
does not contain a search loop, a candidate prompt, or a selection rule: all of
that lives inside EAR (`agent/run_repo.py`, `agent/engine/repo_domain.py`,
`agent/engine/domain.py`), which runs its own Kernel Thompson Sampling loop over
diff-shaped candidates and decides for itself what to try and when to stop.

The adapter's only responsibilities here are the two things EAR must not know:
which paths this benchmark declares editable, and how to reach the host-owned
DEV evaluator. The evaluator is injected as a plain callable, so EAR never
learns that Harbor exists.

The task text is the canonical `terminal-bench-ao` specification — byte-for-byte
the same string codex and claude receive — so no Agent gets a task framing the
others do not.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from agent.run_repo import run_repo_search

from ...protocol import write_json_exclusive
from ...task_specs import task_spec_text
from .model_config import outer_model_parameters
from .repository_tools import ALLOWED_PATHS, evaluate_dev


def _dev_evaluator(dev_command: str):
    """Adapt the host DEV capability to EAR's injected-evaluator contract.

    `evaluate_dev` performs the adapter-side integrity checks (the response
    carries the required fields and the frozen 36-task denominator). EAR
    receives only a score plus the opaque payload.
    """
    from agent.engine.repo_domain import EvaluationResult

    def evaluate(workspace: Path) -> EvaluationResult:
        payload = evaluate_dev(workspace, dev_command)
        return EvaluationResult(score=float(payload["pass_rate"]), feedback=payload)

    return evaluate


def run_native_loop(
    *,
    workspace: Path,
    output_dir: Path,
    dev_command: str,
    model: str,
    seed: int,
    timeout: int,
    max_steps: int = 50,
) -> dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    result_path = output_dir / "native-result.json"
    if result_path.exists():
        raise RuntimeError(f"refusing to overwrite EAR result: {result_path}")

    model_parameters = outer_model_parameters()
    temperature = model_parameters.get("temperature")

    payload = run_repo_search(
        workspace=workspace,
        task_description=task_spec_text("terminal-bench-ao"),
        editable_paths=tuple(ALLOWED_PATHS),
        evaluator=_dev_evaluator(dev_command),
        output_dir=output_dir,
        model=model,
        max_steps=max_steps,
        timeout=timeout,
        # DEV pass rate: higher is better.
        metric_sign=1,
        temperature=None if temperature is None else float(temperature),
        seed=seed,
    )
    # Integrity check: the report must show that the run really went through
    # EAR's Kernel Thompson Sampling selector rather than some fallback path.
    # This is the whole point of the launcher — if it ever stops holding, the
    # Agent was not driving its own control loop and the result is not an EAR
    # result.
    if payload.get("native_selection") != "agent.engine.thompson.select_parent":
        raise RuntimeError(
            "EAR did not run its native Kernel Thompson Sampling loop: "
            f"native_selection={payload.get('native_selection')!r}"
        )

    write_json_exclusive(result_path, payload)
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--dev-command", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--timeout", type=int, required=True)
    args = parser.parse_args(argv)
    os.environ["EAR_SEED"] = str(args.seed)
    run_native_loop(**vars(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["run_native_loop"]
