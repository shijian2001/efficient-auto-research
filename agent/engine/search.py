"""
Kernel Thompson Sampling search loop.

Each step:
  1. Thompson Sampling selects parent node (or start fresh)
  2. Generate plan + code via LLM
  3. Execute code
  4. Create new Attempt node, add to graph (with edges)
  5. Repeat
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from agent.engine.graph import SearchGraph, Attempt
from agent.engine.thompson import select_parent
from agent.engine.embedder import embed_attempt
from agent.engine.executor import Executor
from agent.llm import query as llm_query

logger = logging.getLogger("AutoResearch")

# Code version stamped into report.json so every run is traceable to its commit.
# Resolved from this source file's own repo (the running code), not the cwd —
# under worktree-based iteration the two differ. Cached: git is invoked once.
_GIT_INFO: dict | None = None


def _git_info() -> dict:
    """Return {'commit', 'branch', 'dirty'} for the repo holding this source file.

    All fields fall back to None if git is unavailable or this file is not in a
    git repo (e.g. shipped without .git). Never raises.
    """
    global _GIT_INFO
    if _GIT_INFO is not None:
        return _GIT_INFO

    repo_dir = Path(__file__).resolve().parent

    def _run(args: list[str]) -> str | None:
        try:
            out = subprocess.run(
                ["git", "-C", str(repo_dir), *args],
                capture_output=True, text=True, timeout=10,
            )
            return out.stdout.strip() if out.returncode == 0 else None
        except Exception:
            return None

    commit = _run(["rev-parse", "HEAD"])
    branch = _run(["rev-parse", "--abbrev-ref", "HEAD"])
    status = _run(["status", "--porcelain"])
    _GIT_INFO = {
        "commit": commit,
        "branch": branch,
        "dirty": bool(status) if status is not None else None,
    }
    return _GIT_INFO


@dataclass
class SearchConfig:
    max_steps: int = 50
    time_limit: int = 43200
    model: str = "gpt-4o"
    temperature: float = 0.7
    exec_timeout: int = 3600  # per-step code execution timeout


class GraphSearchEngine:
    """Main search engine using Kernel Thompson Sampling."""

    def __init__(self, task_desc: str, data_dir: Path, work_dir: Path, config: SearchConfig):
        self.task_desc = task_desc
        self.data_dir = data_dir
        self.work_dir = work_dir
        self.config = config

        self.graph = SearchGraph()
        self.executor = Executor(work_dir=work_dir, timeout=config.exec_timeout)

        self.best_attempt: Attempt | None = None
        self.best_metric: float | None = None
        self.start_time: float | None = None
        self.total_in_tokens = 0
        self.total_out_tokens = 0
        self._data_preview: str | None = None
        self._step_log: list[dict] = []
        self._sample_submission_path: Path | None = None
        self._submission_paths: dict[str, str] = {}
        # Consecutive steps since the global best last improved. Feeds the
        # stagnation-adaptive exploration temperature in Kernel TS.
        self._stagnation = 0

    def run(self) -> Path | None:
        """Run search loop. Returns path to best submission or None."""
        self.start_time = time.time()
        submission_path = self.work_dir / "submission.csv"
        best_submission_path = self.work_dir / "best_submission.csv"

        for step in range(self.config.max_steps):
            if time.time() - self.start_time > self.config.time_limit:
                logger.info(f"Time limit reached at step {step}")
                break

            # Thompson sampling selects parent (variance heated if best stagnant)
            parent_id = select_parent(self.graph, self._stagnation)
            parent = self.graph.attempts.get(parent_id) if parent_id else None
            logger.info(f"[Step {step}] parent={parent_id}, best={self.best_metric}, stagnation={self._stagnation}")

            # Generate and execute
            attempt = self._step(parent, step)
            if attempt is None:
                continue

            # Add to graph
            self.graph.add_attempt(attempt)

            # Track best + maintain stagnation counter (drives TS exploration temp).
            improved = (
                attempt.metric is not None
                and (self.best_metric is None or attempt.metric > self.best_metric)
            )
            if improved:
                self.best_metric = attempt.metric
                self.best_attempt = attempt
                saved_submission = self._submission_paths.get(attempt.id)
                if saved_submission:
                    shutil.copy2(saved_submission, best_submission_path)
                logger.info(f"  New best: {self.best_metric:.4f}")
                self._stagnation = 0
            else:
                # No improvement (worse metric or failed step) — search is stuck.
                self._stagnation += 1

            # Log step for efficiency curve (write to disk immediately for observability)
            self._step_log.append({
                "step": step,
                "parent_id": parent_id,
                "metric": attempt.metric,
                "error": attempt.error,
                "best_so_far": self.best_metric,
                "cumulative_tokens": self.total_in_tokens + self.total_out_tokens,
                "elapsed_seconds": time.time() - self.start_time,
            })
            self._save_report()

        # Ensure the final submission is the already validated best artifact.
        if best_submission_path.exists():
            shutil.copy2(best_submission_path, submission_path)
        elif self.best_attempt and self.best_attempt.code:
            logger.info(f"Re-running best attempt (metric={self.best_metric}) to produce final submission")
            self.executor.run(self.best_attempt.code, filename="best_final.py")

        # Side artifact: top-K ensemble of the best validated submissions.
        # The main submission.csv stays the single best (locally validated);
        # the ensemble cannot be scored locally, so it ships alongside for
        # offline comparison rather than replacing the primary artifact.
        try:
            self._ensemble_top_k(k=5)
        except Exception as exc:
            logger.warning(f"Top-K ensemble failed (non-fatal): {exc}")

        self._save_report()
        return submission_path if submission_path.exists() else None

    def _step(self, parent: Attempt | None, step: int) -> Attempt | None:
        """Execute one step: plan → code → execute → create Attempt."""
        attempt_id = uuid.uuid4().hex[:12]

        # Generate plan
        plan, in_tok, out_tok = self._generate_plan(parent)
        self.total_in_tokens += in_tok
        self.total_out_tokens += out_tok
        if not plan:
            return None

        # Generate code
        code, in_tok, out_tok = self._generate_code(parent, plan)
        self.total_in_tokens += in_tok
        self.total_out_tokens += out_tok
        if not code:
            return None

        # Execute
        result = self.executor.run(code, filename=f"step_{step:03d}.py")

        # Parse metric and validate the produced submission artifact.
        metric = None
        error = None
        submission_copy = None
        if result.success:
            metric = self._parse_metric(result.stdout)
            if metric is None:
                error = "MetricMissingError: stdout did not contain final METRIC=<score>"
            else:
                valid_submission, validation_error, submission_copy = self._validate_and_save_submission(step, attempt_id)
                if not valid_submission:
                    metric = None
                    error = validation_error
        else:
            error = self._parse_error(result.stderr)

        if submission_copy:
            self._submission_paths[attempt_id] = str(submission_copy)

        # Persist full per-step trace for later analysis (plan + code + exec I/O).
        self._save_step_trace(step, attempt_id, parent, plan, code, result, metric, error, submission_copy)

        # Compute embedding
        embedding = embed_attempt(plan, code, metric, error)

        return Attempt(
            id=attempt_id,
            plan=plan,
            code=code,
            metric=metric,
            error=error,
            parent_id=parent.id if parent else None,
            embedding=embedding,
        )

    # --- LLM interaction ---

    def _generate_plan(self, parent: Attempt | None) -> tuple[str, int, int]:
        """Generate a brief plan."""
        system = "You are a Kaggle Grandmaster. Output ONLY a brief plan (3-5 sentences): what you will do, why it suits this task, and how you will validate. No code."
        user = self._build_plan_prompt(parent)
        text, in_tok, out_tok = llm_query(system, user, model=self.config.model)
        return text.strip(), in_tok, out_tok

    def _generate_code(self, parent: Attempt | None, plan: str) -> tuple[str, int, int]:
        """Generate complete Python code."""
        system = self._build_code_system()
        user = self._build_code_user(parent, plan)
        total_in = 0
        total_out = 0
        last_text = ""
        for retry in range(20):
            text, in_tok, out_tok = llm_query(system, user, model=self.config.model)
            total_in += in_tok
            total_out += out_tok
            last_text = text
            code = self._extract_code(text)
            if code:
                return code, total_in, total_out
            user = (
                self._build_code_user(parent, plan)
                + "\n\nYour previous response did not contain a valid Python code block. "
                "Return ONLY one fenced ```python code block with a complete executable script. "
                "Do not include explanations, markdown outside the code block, or diffs."
            )
        logger.warning("Failed to extract a valid Python code block. Last response starts with: %r", last_text[:200])
        return "", total_in, total_out

    # --- Prompt construction ---

    def _build_plan_prompt(self, parent: Attempt | None) -> str:
        parts = [f"Task:\n{self.task_desc}\n"]

        if parent is None:
            # New draft: show root attempts (each represents a distinct direction)
            roots = self.graph.get_roots()
            if roots:
                parts.append("Previous directions tried (you MUST propose something fundamentally different):")
                for a in roots:
                    best_in_tree = self._best_metric_in_subtree(a.id)
                    status = f"best metric={best_in_tree:.4f}" if best_in_tree else f"failed: {a.error}"
                    parts.append(f"  - {a.plan} → {status}")
                parts.append("\nPropose a NOVEL strategy that explores an untried direction. Not minor variations.")
            else:
                parts.append("Design a simple, robust first approach. Avoid overly complex models.")
        else:
            parts.append(f"Current approach: {parent.plan}")
            if parent.metric is not None:
                parts.append(f"Current metric: {parent.metric:.4f}")
                parts.append("Propose a MEANINGFUL improvement (not cosmetic). What specific change will increase the score?")
            elif parent.error:
                parts.append(f"Error: {parent.error}")
                # Graph context: find fixes from similar nodes
                for s in self.graph.most_similar(parent.id):
                    for child in self.graph.get_children(s.id):
                        if child.metric is not None:
                            parts.append(f"A similar attempt was fixed by: {child.plan}")
                            break
                    else:
                        continue
                    break
                parts.append("Explain the root cause and how to fix it. Keep the fix minimal.")

        # Always include known errors to avoid repeating them
        errors = self._collect_known_errors()
        if errors:
            parts.append("\nKnown errors to AVOID:\n" + "\n".join(f"  - {e}" for e in errors))

        return "\n".join(parts)

    def _build_code_system(self) -> str:
        data_dir_abs = str(self.data_dir.resolve())
        submission_abs = str((self.work_dir / "submission.csv").resolve())
        cache_abs = str((self.work_dir / "cache").resolve())
        return f"""You are a Kaggle Grandmaster. Write a COMPLETE, competition-winning Python script.

Data & Output:
- Read data from: {data_dir_abs}
- Save submission CSV to EXACTLY: {submission_abs}
- The VERY LAST line of stdout MUST be: print(f'METRIC={{score}}')

Environment:
- Available packages: numpy, pandas, scikit-learn, xgboost, lightgbm, torch, torchvision, transformers, scipy, statsmodels, and others. All pre-installed.
- For neural networks, use PyTorch.
- Your code must finish within {self.config.exec_timeout} seconds.
- All data is already prepared in the data directory. No need to download or unzip anything.
- Do NOT use tqdm or progress bars. Do NOT access the internet.
- Output exactly one fenced ```python code block and nothing else.
- Keep the script concise and practical, preferably under 250 lines. Avoid long comments, verbose logging, or multiple alternative pipelines.

Persistent cache (reuse across steps to save time):
- {cache_abs} persists across steps (create it with os.makedirs(..., exist_ok=True)).
- Expensive intermediate artifacts that do not change between attempts can be
  cached there and loaded instead of recomputed — e.g. a model fine-tuned on
  external/auxiliary data, precomputed features/embeddings, or tokenized inputs.
- Guard every cache use: load if the file exists AND matches the current config
  (encode key settings into the filename), otherwise recompute and save. Never
  let a stale or partial cache corrupt results; correctness comes first.
- This frees the time budget to try more ideas rather than repeat identical work.

Quality Requirements:
- Split data FIRST, then fit all transformers on train only (prevent data leakage)
- Use proper cross-validation for the metric
- Match the sample submission file's format exactly (check column names and dtypes in Data Preview)
- NO progress bars (no tqdm). Minimal prints. ONLY the final METRIC line matters.
- Handle missing values and mixed types explicitly before modeling
{self._error_warning()}"""

    def _build_code_user(self, parent: Attempt | None, plan: str) -> str:
        parts = [f"## Task\n{self.task_desc}"]

        preview = self._get_data_preview()
        if preview:
            parts.append(f"\n## Data Preview\n{preview}")

        parts.append(f"\n## Plan\n{plan}")

        if parent and parent.code:
            if parent.error:
                parts.append(f"\n## Buggy Code\n```python\n{parent.code}\n```")
                parts.append(f"\n## Error\n{parent.error}")
            else:
                if parent.metric is not None:
                    parts.append(
                        f"\n## Current Code (metric={parent.metric:.4f} — this pipeline WORKS)\n"
                        f"```python\n{parent.code}\n```"
                    )
                    parts.append(
                        "\nInstructions: MODIFY the working code above. Keep the overall "
                        "pipeline intact; change or add ONE component as described in the plan. "
                        "Do NOT rewrite from scratch. Return the complete modified script."
                    )
                else:
                    parts.append(f"\n## Current Code (improve this)\n```python\n{parent.code}\n```")

        return "\n".join(parts)

    # --- Utilities ---

    def _collect_known_errors(self, limit: int = 8) -> list[str]:
        """Collect errors from graph, aggregated by exception type.

        Raw error strings rarely transfer to freshly written code, so repeated
        failures of the same type are merged into one line with a count and a
        generic lesson — the model learns the pitfall class, not one instance.
        """
        by_type: dict[str, list[str]] = {}
        for aid in self.graph.node_ids:  # node_ids preserves insertion order
            a = self.graph.attempts[aid]
            if not a.error:
                continue
            m = re.search(r"\b(\w+Error)\b", a.error)
            key = m.group(1) if m else a.error.split(":")[0][:40]
            by_type.setdefault(key, []).append(a.error)

        lessons = {
            "TypeError": "defensively validate types/shapes before operating on them",
            "ValueError": "check array shapes, unpack counts, and category coverage first",
            "KeyError": "verify keys/columns exist before indexing",
            "IndexError": "guard index bounds; don't assume non-empty results",
            "FileNotFoundError": "list actual files first; never hard-code paths or extensions",
            "StopIteration": "handle empty iterators from parsing/lookup misses",
            "TimeoutError": "budget runtime; subsample or reduce epochs to finish in time",
            "MemoryError": "reduce batch/feature size; process in chunks",
        }

        out = []
        # Most recent error types last in insertion order → keep the newest ones
        for key, msgs in list(by_type.items())[-limit:]:
            latest = msgs[-1].strip().replace("\n", " ")[:200]
            count = f" ({len(msgs)}x)" if len(msgs) > 1 else ""
            lesson = lessons.get(key)
            suffix = f" — lesson: {lesson}" if lesson else ""
            out.append(f"{key}{count}: {latest}{suffix}")
        return out

    def _error_warning(self) -> str:
        """Format error warning for code generation prompt."""
        errors = self._collect_known_errors()
        if not errors:
            return ""
        lines = "\n".join(f"  - {e}" for e in errors)
        return f"\nCRITICAL - Your code MUST NOT trigger these errors (seen in previous attempts):\n{lines}"

    def _best_metric_in_subtree(self, root_id: str) -> float | None:
        """Find the best metric in the entire subtree rooted at root_id (BFS)."""
        best = self.graph.attempts[root_id].metric
        queue = [root_id]
        while queue:
            node_id = queue.pop(0)
            for child in self.graph.get_children(node_id):
                if child.metric is not None and (best is None or child.metric > best):
                    best = child.metric
                queue.append(child.id)
        return best

    def _get_data_preview(self) -> str:
        if self._data_preview is not None:
            return self._data_preview
        parts = []
        # Show all csv files (header + first rows, read efficiently)
        for fpath in sorted(self.data_dir.glob("*.csv")):
            try:
                lines = []
                with open(fpath) as f:
                    for _ in range(4):
                        line = f.readline()
                        if not line:
                            break
                        lines.append(line.rstrip())
                if lines:
                    parts.append(f"{fpath.name}:\n" + "\n".join(lines))
            except Exception:
                pass
        # List non-csv files
        other = [f.name for f in self.data_dir.iterdir() if f.suffix not in (".md", ".csv")]
        if other:
            parts.append(f"Other files: {', '.join(other)}")
        sample_path = self._find_sample_submission()
        if sample_path:
            parts.append(f"Sample submission path: {sample_path}")
        self._data_preview = "\n\n".join(parts) if parts else ""
        return self._data_preview

    def _extract_code(self, text: str) -> str | None:
        match = re.search(r"```\s*(?:python|py)\s*\n(.*?)```", text, re.DOTALL | re.IGNORECASE)
        if match:
            code = match.group(1).strip()
            return code if self._is_plausible_python(code) else None
        return None

    def _is_plausible_python(self, code: str) -> bool:
        if not code:
            return False
        forbidden_markers = ("```", "diff --git", "*** Begin Patch", "SEARCH/REPLACE", "<<<<<<<", ">>>>>>>")
        if any(marker in code for marker in forbidden_markers):
            return False
        first_line = code.lstrip().splitlines()[0].strip().lower()
        prose_prefixes = ("looking at", "here is", "here's", "i will", "we need", "the code")
        if first_line.startswith(prose_prefixes):
            return False
        try:
            compile(code, "<llm_code>", "exec")
        except SyntaxError:
            return False
        return True

    def _parse_error(self, stderr: str) -> str | None:
        """Extract the error line from stderr (find the actual exception)."""
        if not stderr:
            return None
        for line in reversed(stderr.strip().splitlines()):
            stripped = line.strip()
            if stripped and ("Error" in stripped or "Exception" in stripped) and not stripped.startswith("File"):
                return stripped
        # Fallback: last non-empty line
        for line in reversed(stderr.strip().splitlines()):
            if line.strip():
                return line.strip()
        return None

    def _parse_metric(self, stdout: str) -> float | None:
        for line in reversed(stdout.strip().splitlines()):
            match = re.search(r"METRIC\s*=\s*([\d.eE+-]+)", line)
            if match:
                try:
                    return float(match.group(1))
                except ValueError:
                    pass
        return None

    def _find_sample_submission(self) -> Path | None:
        """Find the sample submission file, preferring exact obvious names."""
        if self._sample_submission_path is not None:
            return self._sample_submission_path

        candidates = [
            p for p in self.data_dir.rglob("*.csv")
            if "sample" in p.name.lower() and "submission" in p.name.lower()
        ]
        if candidates:
            candidates.sort(key=lambda p: (len(p.parts), len(p.name), str(p)))
            self._sample_submission_path = candidates[0]
        return self._sample_submission_path

    def _read_csv_header_and_count(self, path: Path) -> tuple[list[str], int]:
        import csv

        with path.open(newline="") as f:
            reader = csv.reader(f)
            try:
                header = next(reader)
            except StopIteration:
                return [], 0
            count = sum(1 for _ in reader)
        return header, count

    def _validate_and_save_submission(self, step: int, attempt_id: str) -> tuple[bool, str | None, Path | None]:
        """Validate current submission.csv and persist a per-attempt copy if valid."""
        submission_path = self.work_dir / "submission.csv"
        if not submission_path.exists():
            return False, "SubmissionMissingError: submission.csv was not created", None

        sample_path = self._find_sample_submission()
        if sample_path is None:
            saved = self._copy_step_submission(step, attempt_id, submission_path)
            return True, None, saved

        try:
            sample_header, sample_rows = self._read_csv_header_and_count(sample_path)
            submission_header, submission_rows = self._read_csv_header_and_count(submission_path)
        except Exception as exc:
            return False, f"SubmissionFormatError: failed to read submission CSV: {exc}", None

        if not submission_header:
            return False, "SubmissionFormatError: submission.csv is empty", None
        if submission_header != sample_header:
            return False, (
                "SubmissionFormatError: column mismatch. "
                f"expected={sample_header}, got={submission_header}"
            ), None
        if sample_rows and submission_rows != sample_rows:
            return False, (
                "SubmissionFormatError: row count mismatch. "
                f"expected={sample_rows}, got={submission_rows}"
            ), None
        content_error = self._check_submission_content(submission_path, submission_header)
        if content_error:
            return False, content_error, None

        saved = self._copy_step_submission(step, attempt_id, submission_path)
        return True, None, saved

    def _copy_step_submission(self, step: int, attempt_id: str, submission_path: Path) -> Path:
        submission_dir = self.work_dir / "submissions"
        submission_dir.mkdir(parents=True, exist_ok=True)
        saved = submission_dir / f"step_{step:03d}_{attempt_id}.csv"
        shutil.copy2(submission_path, saved)
        return saved

    def _check_submission_content(self, path: Path, header: list[str]) -> str | None:
        """Catch empty or placeholder prediction values without rejecting simple baselines."""
        import csv

        if len(header) < 2:
            return None
        prediction_columns = header[1:]
        checked_rows = 0

        with path.open(newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                checked_rows += 1
                for column in prediction_columns:
                    value = (row.get(column) or "").strip()
                    if value == "":
                        return f"SubmissionContentError: empty value in prediction column {column!r}"
                    if value.lower() in {"nan", "none", "null", "placeholder", "todo"}:
                        return f"SubmissionContentError: placeholder value {value!r} in {column!r}"
                if checked_rows >= 1000:
                    break

        if checked_rows == 0:
            return "SubmissionContentError: submission has no prediction rows"
        return None

    def _ensemble_top_k(self, k: int = 5) -> Path | None:
        """Weighted-average ensemble of the top-k validated per-attempt submissions.

        Only numeric prediction columns are fused (weights = each attempt's metric).
        If any prediction column is non-numeric (e.g. free-text answers), or fewer
        than 2 candidates exist, no ensemble is produced. Output goes to
        workspace/ensemble_submission.csv as a side artifact; the primary
        submission.csv is never replaced here.
        """
        import pandas as pd

        scored = [
            (a.metric, self._submission_paths.get(a.id))
            for a in self.graph.attempts.values()
            if a.metric is not None and self._submission_paths.get(a.id)
        ]
        scored = [(m, p) for m, p in scored if Path(p).exists()]
        scored.sort(key=lambda t: t[0], reverse=True)
        top = scored[:k]
        if len(top) < 2:
            logger.info(f"Ensemble skipped: only {len(top)} scored submissions")
            return None

        frames = []
        weights = []
        id_col = None
        pred_cols = None
        for metric, path in top:
            df = pd.read_csv(path)
            if id_col is None:
                id_col = df.columns[0]
                pred_cols = list(df.columns[1:])
            if list(df.columns) != [id_col] + pred_cols:
                logger.info("Ensemble skipped: column mismatch across submissions")
                return None
            numeric = df[pred_cols].apply(pd.to_numeric, errors="coerce")
            if numeric.isna().any().any():
                logger.info("Ensemble skipped: non-numeric prediction values (text task?)")
                return None
            df[pred_cols] = numeric
            frames.append(df.set_index(id_col))
            weights.append(metric)

        base_index = frames[0].index
        if any(not f.index.equals(base_index) for f in frames[1:]):
            # Align by ID; bail out if IDs don't fully overlap
            common = base_index
            for f in frames[1:]:
                common = common.intersection(f.index)
            if len(common) != len(base_index):
                logger.info("Ensemble skipped: submission ID sets differ")
                return None
            frames = [f.loc[base_index] for f in frames]

        w = pd.Series(weights, dtype=float)
        w = w / w.sum()
        fused = sum(f[pred_cols] * wi for f, wi in zip(frames, w))
        out = fused.reset_index()
        out.columns = [id_col] + pred_cols

        ensemble_path = self.work_dir / "ensemble_submission.csv"
        out.to_csv(ensemble_path, index=False)

        # Reuse existing content check as a final self-check
        content_error = self._check_submission_content(ensemble_path, [id_col] + pred_cols)
        if content_error:
            logger.warning(f"Ensemble self-check failed ({content_error}); removing artifact")
            ensemble_path.unlink(missing_ok=True)
            return None

        logger.info(
            f"Ensemble written: {ensemble_path.name} from top-{len(top)} "
            f"(metrics {', '.join(f'{m:.4f}' for m, _ in top)})"
        )
        return ensemble_path

    def _save_step_trace(self, step, attempt_id, parent, plan, code, result, metric, error, submission_copy=None):
        """Persist the full reasoning + execution trace of one step to disk.

        report.json only keeps the parsed metric/error; this captures the LLM plan,
        the generated code, and the raw stdout/stderr so a run can be fully analyzed.
        """
        trace_dir = self.work_dir / "traces"
        trace_dir.mkdir(parents=True, exist_ok=True)
        trace = {
            "step": step,
            "attempt_id": attempt_id,
            "parent_id": parent.id if parent else None,
            "parent_plan": parent.plan if parent else None,
            "parent_metric": parent.metric if parent else None,
            "plan": plan,
            "code": code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
            "exec_time": result.exec_time,
            "timed_out": result.timed_out,
            "metric": metric,
            "error": error,
            "submission_copy": str(submission_copy) if submission_copy else None,
            "best_so_far": self.best_metric,
            "elapsed_seconds": time.time() - self.start_time if self.start_time else 0,
        }
        path = trace_dir / f"step_{step:03d}.json"
        path.write_text(json.dumps(trace, indent=2))

    def _save_report(self):
        git = _git_info()
        report = {
            "git_commit": git["commit"],
            "git_branch": git["branch"],
            "git_dirty": git["dirty"],
            "total_steps": len(self.graph.attempts),
            "best_metric": self.best_metric,
            "total_in_tokens": self.total_in_tokens,
            "total_out_tokens": self.total_out_tokens,
            "total_tokens": self.total_in_tokens + self.total_out_tokens,
            "total_time_seconds": time.time() - self.start_time if self.start_time else 0,
            "graph_nodes": len(self.graph.attempts),
            "step_log": self._step_log,
        }
        path = self.work_dir / "report.json"
        path.write_text(json.dumps(report, indent=2))
        logger.info(f"Report saved: {path}")
