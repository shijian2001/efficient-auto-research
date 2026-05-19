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

    def run(self) -> Path | None:
        """Run search loop. Returns path to best submission or None."""
        self.start_time = time.time()
        submission_path = self.work_dir / "submission.csv"

        for step in range(self.config.max_steps):
            if time.time() - self.start_time > self.config.time_limit:
                logger.info(f"Time limit reached at step {step}")
                break

            # Thompson sampling selects parent
            parent_id = select_parent(self.graph)
            parent = self.graph.attempts.get(parent_id) if parent_id else None
            logger.info(f"[Step {step}] parent={parent_id}, best={self.best_metric}")

            # Generate and execute
            attempt = self._step(parent, step)
            if attempt is None:
                continue

            # Add to graph
            self.graph.add_attempt(attempt)

            # Track best
            if attempt.metric is not None:
                if self.best_metric is None or attempt.metric > self.best_metric:
                    self.best_metric = attempt.metric
                    self.best_attempt = attempt
                    logger.info(f"  New best: {self.best_metric:.4f}")

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

        # Ensure the best attempt's submission is the final one
        if self.best_attempt and self.best_attempt.code:
            logger.info(f"Re-running best attempt (metric={self.best_metric}) to produce final submission")
            self.executor.run(self.best_attempt.code, filename="best_final.py")

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

        # Parse metric and error
        metric = self._parse_metric(result.stdout) if result.success else None
        error = self._parse_error(result.stderr) if not result.success else None

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
        text, in_tok, out_tok = llm_query(system, user, model=self.config.model, max_tokens=300)
        return text.strip(), in_tok, out_tok

    def _generate_code(self, parent: Attempt | None, plan: str) -> tuple[str, int, int]:
        """Generate complete Python code."""
        system = self._build_code_system()
        user = self._build_code_user(parent, plan)
        text, in_tok, out_tok = llm_query(system, user, model=self.config.model, max_tokens=8192)
        code = self._extract_code(text)
        return code, in_tok, out_tok

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
                parts.append(f"\n## Current Code (improve this)\n```python\n{parent.code}\n```")
                if parent.metric is not None:
                    parts.append(f"\n## Current Metric: {parent.metric:.4f}")

        return "\n".join(parts)

    # --- Utilities ---

    def _collect_known_errors(self) -> list[str]:
        """Collect unique errors from graph."""
        errors = set()
        for a in self.graph.attempts.values():
            if a.error:
                errors.add(a.error)
        return list(errors)

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
        self._data_preview = "\n\n".join(parts) if parts else ""
        return self._data_preview

    def _extract_code(self, text: str) -> str:
        match = re.search(r"```python\s*\n(.*?)```", text, re.DOTALL)
        if match:
            return match.group(1).strip()
        match = re.search(r"```\s*\n(.*?)```", text, re.DOTALL)
        if match:
            return match.group(1).strip()
        return text.strip()

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

    def _save_report(self):
        report = {
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
