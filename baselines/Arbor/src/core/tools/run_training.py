"""RunTraining tool — execute long-running training/eval commands with built-in
progress monitoring, eliminating the need for sleep-poll LLM turns.

Designed for executors that launch multi-fold training, hyperparameter sweeps,
or evaluation scripts that can take 10-60+ minutes.
"""

from __future__ import annotations

import asyncio
import copy
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from .base import Tool
from .bash import terminate_process

# ---------------------------------------------------------------------------
# Metric extraction patterns
# ---------------------------------------------------------------------------

_DEFAULT_METRIC_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("loss", re.compile(r"(?:loss|train_loss|val_loss)\s*[:=]\s*([\d.]+(?:e[+-]?\d+)?)", re.IGNORECASE)),
    ("auc", re.compile(r"(?:auc|auroc|val_auc|roc_auc)\s*[:=]\s*([\d.]+)", re.IGNORECASE)),
    ("accuracy", re.compile(r"(?:acc|accuracy|val_acc|val_accuracy)\s*[:=]\s*([\d.]+)", re.IGNORECASE)),
    ("rmse", re.compile(r"(?:rmse|val_rmse)\s*[:=]\s*([\d.]+)", re.IGNORECASE)),
    ("f1", re.compile(r"(?:f1|f1_score|val_f1)\s*[:=]\s*([\d.]+)", re.IGNORECASE)),
    ("logloss", re.compile(r"(?:logloss|log_loss|val_logloss)\s*[:=]\s*([\d.]+)", re.IGNORECASE)),
]

_FOLD_PATTERN = re.compile(r"(?:fold|Fold|FOLD)\s*(\d+)", re.IGNORECASE)
_EPOCH_PATTERN = re.compile(r"(?:epoch|Epoch|EPOCH)\s*(\d+)", re.IGNORECASE)
_FOLD_COMPLETE = re.compile(r"(?:fold|Fold)\s*(\d+).*(?:complete|done|finished|best|saved)", re.IGNORECASE)
_TRAINING_DONE = re.compile(r"(?:training\s+(?:complete|done|finished)|all\s+folds\s+(?:complete|done))", re.IGNORECASE)


@dataclass
class MetricEvent:
    wall_seconds: float
    event_type: str  # "metric", "fold_complete", "epoch", "training_done"
    label: str
    value: str


@dataclass
class TrainingProgress:
    events: list[MetricEvent] = field(default_factory=list)
    current_fold: int | None = None
    current_epoch: int | None = None
    last_metrics: dict[str, str] = field(default_factory=dict)


class RunTrainingTool(Tool):
    name = "RunTraining"
    description = (
        "Run a long training/evaluation command and monitor progress without "
        "consuming LLM turns. Blocks until completion and returns structured results.\n"
        "\n"
        "Use this INSTEAD of Bash for commands that take >5 minutes "
        "(multi-fold training, large-scale evaluation, hyperparameter sweeps).\n"
        "\n"
        "Returns:\n"
        "- Exit code and wall-clock duration\n"
        "- Detected metrics timeline (loss, accuracy, AUC, etc. per epoch/fold)\n"
        "- Final output (last 200 lines of stdout)\n"
        "\n"
        "This is the PREFERRED way to run training. Do NOT use Bash with "
        "sleep polling for training runs — use this tool instead.\n"
        "\n"
        "If the command times out, you still get all metrics captured up to "
        "that point plus the partial output."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The training/eval command to run.",
            },
            "timeout": {
                "type": "integer",
                "description": (
                    "Timeout in seconds (default 86400, max 604800). "
                    "Set generously — the tool blocks until completion or timeout. "
                    "For multi-fold training, estimate: epochs * time_per_epoch * n_folds * 1.5"
                ),
            },
            "budget_stage": {
                "type": "string",
                "description": (
                    "Optional fidelity stage such as smoke, pilot, or full. "
                    "If timeout is omitted and the stage is configured, the stage walltime is used."
                ),
            },
            "log_file": {
                "type": "string",
                "description": (
                    "Optional path to write full output log. "
                    "If not set, logs go to .arbor/training_logs/."
                ),
            },
            "metric_patterns": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Optional additional regex patterns to extract metrics. "
                    "Each pattern should have one capture group for the value. "
                    "Default patterns already detect: loss, auc, accuracy, rmse, f1, logloss, "
                    "fold/epoch markers."
                ),
            },
        },
        "required": ["command"],
    }
    is_read_only = False
    max_result_chars = 100_000

    def __init__(
        self,
        *,
        cwd: str,
        timeout_default: int = 86_400,
        timeout_max: int = 604_800,
        stage_timeouts: dict[str, int] | None = None,
        stall_timeout: int | None = 1_800,
        **kwargs: Any,
    ):
        super().__init__(cwd=cwd, **kwargs)
        self.timeout_default = max(1, timeout_default)
        self.timeout_max = max(self.timeout_default, timeout_max)
        # Idle timeout: a job that emits no output for this long is treated as
        # hung and terminated early, keeping partial metrics (#8). None = off.
        self.stall_timeout = stall_timeout if (stall_timeout is None or stall_timeout > 0) else None
        self.stage_timeouts = {
            str(name): min(max(1, int(timeout)), self.timeout_max)
            for name, timeout in (stage_timeouts or {}).items()
        }
        self.input_schema = copy.deepcopy(self.input_schema)
        self.input_schema["properties"]["timeout"]["description"] = (
            f"Timeout in seconds (default {self.timeout_default}, max {self.timeout_max}). "
            "Set generously — the tool blocks until completion or timeout. "
            "For multi-fold training, estimate: epochs * time_per_epoch * n_folds * 1.5"
        )
        if self.stage_timeouts:
            stages = ", ".join(f"{k}={v}s" for k, v in self.stage_timeouts.items())
            self.input_schema["properties"]["budget_stage"]["enum"] = list(self.stage_timeouts)
            self.input_schema["properties"]["budget_stage"]["description"] = (
                "Optional fidelity stage. If timeout is omitted, uses the configured stage walltime: "
                f"{stages}. Explicit timeout still wins and is capped by max."
            )

    async def execute(self, **kwargs: Any) -> str:
        command: str = kwargs["command"]
        requested_timeout = kwargs.get("timeout")
        budget_stage = kwargs.get("budget_stage")
        if requested_timeout is None and budget_stage in self.stage_timeouts:
            timeout = self.stage_timeouts[budget_stage]
        else:
            timeout_value = self.timeout_default if requested_timeout is None else requested_timeout
            timeout = min(max(1, timeout_value), self.timeout_max)
        log_file: str | None = kwargs.get("log_file")
        extra_patterns: list[str] = kwargs.get("metric_patterns", [])

        # Build metric patterns
        patterns = list(_DEFAULT_METRIC_PATTERNS)
        for pat_str in extra_patterns:
            try:
                patterns.append(("custom", re.compile(pat_str, re.IGNORECASE)))
            except re.error:
                pass

        # Set up log file
        if not log_file:
            persist_root = self.workspace_dir or self.cwd
            log_dir = os.path.join(persist_root, ".arbor", "training_logs")
            os.makedirs(log_dir, exist_ok=True)
            log_file = os.path.join(log_dir, f"train_{uuid.uuid4().hex[:8]}.log")

        log_dir_parent = os.path.dirname(log_file)
        if log_dir_parent:
            os.makedirs(log_dir_parent, exist_ok=True)

        progress = TrainingProgress()
        output_lines: list[str] = []
        _MAX_MEMORY_LINES = 5000
        t0 = time.monotonic()
        timed_out = False
        stalled = False

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=self.cwd,
                env={**os.environ, "PYTHONUNBUFFERED": "1"},
                # New session so terminate_process can kill the whole process
                # group on stall/timeout — otherwise the training grandchild
                # orphans while we only kill the shell (#8).
                start_new_session=True,
            )

            log_fh = open(log_file, "w", encoding="utf-8")

            try:
                assert proc.stdout is not None
                # Read line-by-line, bounding each read by the idle (stall)
                # timeout while still enforcing the overall walltime. A read that
                # times out means either a hang (stall) or the hard cap (#8).
                while True:
                    remaining = timeout - (time.monotonic() - t0)
                    if remaining <= 0:
                        timed_out = True
                        break
                    # Decide which budget this read is bounded by *before* waiting,
                    # so the outcome label can't be misclassified by timing jitter.
                    use_stall = self.stall_timeout is not None and self.stall_timeout < remaining
                    read_window = self.stall_timeout if use_stall else remaining
                    try:
                        line_bytes = await asyncio.wait_for(
                            proc.stdout.readline(), timeout=read_window
                        )
                    except asyncio.TimeoutError:
                        if use_stall:
                            stalled = True
                        else:
                            timed_out = True
                        break
                    if not line_bytes:
                        break  # stdout closed → process finished
                    line = line_bytes.decode("utf-8", errors="replace").rstrip("\n")
                    output_lines.append(line)
                    if len(output_lines) > _MAX_MEMORY_LINES * 2:
                        del output_lines[:-_MAX_MEMORY_LINES]
                    log_fh.write(line + "\n")
                    log_fh.flush()
                    _extract_metrics(line, progress, time.monotonic() - t0, patterns)

                if stalled or timed_out:
                    await terminate_process(proc)
                else:
                    await proc.wait()
            finally:
                log_fh.close()

        except OSError as e:
            return f"ERROR: Failed to start command: {e}"

        elapsed = time.monotonic() - t0
        exit_code = proc.returncode if proc.returncode is not None else -1

        return self.process_result(
            _format_result(
                command=command,
                elapsed=elapsed,
                exit_code=exit_code,
                timed_out=timed_out,
                stalled=stalled,
                stall_timeout=self.stall_timeout,
                progress=progress,
                output_lines=output_lines,
                log_file=log_file,
                timeout=timeout,
            )
        )


def _extract_metrics(
    line: str,
    progress: TrainingProgress,
    wall_seconds: float,
    patterns: list[tuple[str, re.Pattern]],
) -> None:
    """Scan a single output line for metrics and progress markers."""
    # Check fold completion
    m = _FOLD_COMPLETE.search(line)
    if m:
        fold_num = m.group(1)
        # Gather any metrics from this line
        metric_str = _extract_line_metrics(line, patterns)
        progress.events.append(MetricEvent(
            wall_seconds=wall_seconds,
            event_type="fold_complete",
            label=f"Fold {fold_num} complete",
            value=metric_str or "",
        ))
        progress.current_fold = int(fold_num)
        return

    # Check training done
    if _TRAINING_DONE.search(line):
        metric_str = _extract_line_metrics(line, patterns)
        progress.events.append(MetricEvent(
            wall_seconds=wall_seconds,
            event_type="training_done",
            label="Training complete",
            value=metric_str or "",
        ))
        return

    # Check fold start
    m = _FOLD_PATTERN.search(line)
    if m:
        new_fold = int(m.group(1))
        if new_fold != progress.current_fold:
            progress.current_fold = new_fold

    # Check epoch
    m = _EPOCH_PATTERN.search(line)
    if m:
        new_epoch = int(m.group(1))
        if new_epoch != progress.current_epoch:
            progress.current_epoch = new_epoch

    # Extract metrics
    for metric_name, pattern in patterns:
        m = pattern.search(line)
        if m:
            value = m.group(1)
            progress.last_metrics[metric_name] = value
            # Record significant metric events (not every line)
            if any(kw in line.lower() for kw in ("best", "val_", "valid", "eval", "test")):
                fold_str = f" (fold {progress.current_fold})" if progress.current_fold is not None else ""
                epoch_str = f" ep{progress.current_epoch}" if progress.current_epoch is not None else ""
                progress.events.append(MetricEvent(
                    wall_seconds=wall_seconds,
                    event_type="metric",
                    label=f"{metric_name}{fold_str}{epoch_str}",
                    value=value,
                ))


def _extract_line_metrics(line: str, patterns: list[tuple[str, re.Pattern]]) -> str:
    """Extract all metrics from a single line and return as a compact string."""
    found = []
    for metric_name, pattern in patterns:
        m = pattern.search(line)
        if m:
            found.append(f"{metric_name}={m.group(1)}")
    return ", ".join(found)


def _format_duration(seconds: float) -> str:
    """Format seconds as human-readable duration."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    if minutes < 60:
        return f"{minutes}m {secs}s"
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours}h {mins}m {secs}s"


def _format_result(
    *,
    command: str,
    elapsed: float,
    exit_code: int,
    timed_out: bool,
    progress: TrainingProgress,
    output_lines: list[str],
    log_file: str,
    timeout: int,
    stalled: bool = False,
    stall_timeout: int | None = None,
) -> str:
    """Build the structured result string."""
    parts: list[str] = []

    # Header
    if stalled:
        status = "STALLED"
    elif timed_out:
        status = "TIMED OUT"
    elif exit_code != 0:
        status = "FAILED"
    else:
        status = "Complete"
    parts.append(f"## Training {status}")
    parts.append(f"**Command**: `{command[:200]}`")
    parts.append(f"**Duration**: {_format_duration(elapsed)} | **Exit code**: {exit_code}")
    if stalled:
        parts.append(
            f"**Note**: No output for {stall_timeout}s — treated as hung and "
            f"terminated early. The run was NOT given its full {timeout}s budget. "
            f"Partial results below; inspect the log, then fix the hang or re-run."
        )
    elif timed_out:
        parts.append(f"**Note**: Timed out after {timeout}s. Partial results below.")
    parts.append("")

    # Metrics timeline
    if progress.events:
        parts.append("### Metrics Timeline")
        parts.append("| Time | Event | Value |")
        parts.append("|------|-------|-------|")
        # Deduplicate and limit to most important events
        shown_events = _deduplicate_events(progress.events, max_events=30)
        for event in shown_events:
            time_str = _format_duration(event.wall_seconds)
            parts.append(f"| {time_str} | {event.label} | {event.value} |")
        parts.append("")

    # Final metrics summary
    if progress.last_metrics:
        parts.append("### Final Metrics")
        for name, value in progress.last_metrics.items():
            parts.append(f"- **{name}**: {value}")
        parts.append("")

    # Output tail
    tail_lines = 200 if exit_code == 0 else 500
    tail = output_lines[-tail_lines:] if len(output_lines) > tail_lines else output_lines
    parts.append(f"### Output (last {len(tail)} of {len(output_lines)} lines)")
    parts.append("```")
    parts.append("\n".join(tail))
    parts.append("```")
    parts.append("")
    parts.append(f"[Full log: {log_file}]")

    return "\n".join(parts)


def _deduplicate_events(events: list[MetricEvent], max_events: int = 30) -> list[MetricEvent]:
    """Keep the most informative events, prioritizing fold completions and significant metrics."""
    # Always keep fold_complete and training_done events
    priority = [e for e in events if e.event_type in ("fold_complete", "training_done")]
    others = [e for e in events if e.event_type not in ("fold_complete", "training_done")]

    remaining = max_events - len(priority)
    if remaining > 0 and others:
        # Sample evenly from the remaining events
        step = max(1, len(others) // remaining)
        sampled = others[::step][:remaining]
        priority.extend(sampled)

    priority.sort(key=lambda e: e.wall_seconds)
    return priority[:max_events]
