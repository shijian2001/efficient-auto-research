"""Preflight environment checks.

Runs before the user spends any LLM tokens on a research session.
This iteration is check-only: surfaces problems with clear messages but
does not auto-fix. Auto-fix flows (interactive git init, eval scaffolding,
API key prompt) are a follow-up.

Each check returns a CheckResult; PreflightChecker.run_all() returns
True iff all checks passed (status == "pass").
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import typer


@dataclass
class CheckResult:
    name: str
    status: str  # "pass" | "warn" | "fail"
    message: str
    hint: str | None = None  # actionable next step shown when not pass


class PreflightChecker:
    """Run a fixed list of checks and report status.

    Checks:
      1. LLM credentials available (env var or explicit)
      2. cwd exists and contains files
      3. git installed and repo not dirty (warn if no repo at all)
      4. an eval entry point exists (eval.sh / evaluate.py / similar)
      5. contamination signals declared in the eval contract (zero-network warn)
    """

    EVAL_CANDIDATES = ("eval.sh", "evaluate.sh", "run_eval.sh",
                       "evaluate.py", "eval.py")

    def __init__(self, cwd: Path, provider: str | None,
                 explicit_api_key: str | None = None,
                 *, verbose: bool = False,
                 eval_contract: dict | None = None) -> None:
        self.cwd = cwd.resolve()
        self.provider = (provider or "anthropic").lower()
        self.explicit_api_key = explicit_api_key
        self.verbose = verbose
        self.eval_contract = eval_contract or {}

    def run_all(self) -> bool:
        """Print results and return True iff none failed (legacy)."""
        results = self.run_all_collect()
        return all(r.status != "fail" for r in results)

    def check_llm_credentials(self, *, render: bool = True) -> CheckResult:
        """Run just the LLM credential check.

        The intake chat itself needs an LLM call, so the CLI uses this as a
        zero-token gate before constructing the planning agent. Full project
        preflight still runs later against the final target directory.
        """
        result = self._check_llm()
        if render and (self.verbose or result.status == "fail"):
            self._render(result)
        return result

    def run_all_collect(self, *, render: bool = True) -> list[CheckResult]:
        """Run every check, render to stdout, return all results.

        Non-blocking — even fails are returned, not raised. Caller decides
        what to do (the intake agent uses these as initial context to
        discuss with the user).

        By default only ``fail`` results are rendered to keep the launch
        flow quiet — pass/warn pile up as visual noise on repeated runs.
        Set ``verbose=True`` to print every check.
        """
        checks: list[Callable[[], CheckResult]] = [
            self._check_llm,
            self._check_cwd,
            self._check_git,
            self._check_eval,
            self._check_contamination,
        ]
        results: list[CheckResult] = []
        for check in checks:
            result = check()
            if render and (self.verbose or result.status == "fail"):
                self._render(result)
            results.append(result)
        return results

    @staticmethod
    def _render(r: CheckResult) -> None:
        if r.status == "pass":
            typer.secho(f"  [ok]   {r.name}: {r.message}", fg=typer.colors.GREEN)
        elif r.status == "warn":
            typer.secho(f"  [warn] {r.name}: {r.message}", fg=typer.colors.YELLOW)
            if r.hint:
                typer.echo(f"         hint: {r.hint}")
        else:
            typer.secho(f"  [fail] {r.name}: {r.message}", fg=typer.colors.RED, err=True)
            if r.hint:
                typer.secho(f"         hint: {r.hint}", fg=typer.colors.RED, err=True)

    # ── Check 1: LLM credentials ───────────────────────────────────

    _PROVIDER_ENV = {
        "auto": None,      # backend chosen from the model name; either key works
        "anthropic": ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"),
        "openai": ("OPENAI_API_KEY",),
        "openai-responses": ("OPENAI_API_KEY",),
        "openai-chat": ("OPENAI_API_KEY",),
        "litellm": None,  # depends on chosen model
    }

    def _check_llm(self) -> CheckResult:
        if self.provider == "openai-oauth":
            return self._check_openai_oauth()

        if self.provider == "anthropic-oauth":
            return self._check_anthropic_oauth()

        if self.provider not in self._PROVIDER_ENV:
            return CheckResult(
                "llm", "fail",
                f"unknown provider={self.provider}",
                hint="run `arbor setup` and choose anthropic, openai, or litellm",
            )

        if self.explicit_api_key:
            return CheckResult("llm", "pass",
                               f"api key supplied for provider={self.provider}")

        env_vars = self._PROVIDER_ENV.get(self.provider)
        if env_vars is None:
            # litellm or unknown provider — best-effort guess
            for v in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
                if os.environ.get(v):
                    return CheckResult("llm", "pass",
                                       f"found ${v} (provider={self.provider})")
            return CheckResult(
                "llm", "warn",
                f"no obvious api key in environment for provider={self.provider}",
                hint="set ANTHROPIC_API_KEY / OPENAI_API_KEY, or run `arbor setup`",
            )

        for env_var in env_vars:
            if os.environ.get(env_var):
                return CheckResult("llm", "pass",
                                   f"found ${env_var} (provider={self.provider})")
        expected = " or ".join(f"${v}" for v in env_vars)
        primary = env_vars[0]
        if len(env_vars) == 1:
            expected = f"${primary}"
        return CheckResult(
            "llm", "fail",
            f"missing {expected} for provider={self.provider}",
            hint=f"export {primary}=... or run `arbor setup`",
        )

    @staticmethod
    def _check_openai_oauth() -> CheckResult:
        """ChatGPT subscription auth lives in a token file, not an env var."""
        try:
            from ..core.oauth import openai as oauth
        except ImportError:
            return CheckResult(
                "llm", "fail", "openai oauth support unavailable",
                hint="reinstall arbor",
            )
        tokens = oauth.load_tokens()
        if tokens is None:
            return CheckResult(
                "llm", "fail",
                "not logged in to ChatGPT (provider=openai-oauth)",
                hint="run `arbor login openai`",
            )
        plan = tokens.plan_type or "unknown"
        return CheckResult("llm", "pass",
                           f"ChatGPT subscription token found (plan={plan})")

    @staticmethod
    def _check_anthropic_oauth() -> CheckResult:
        """Claude subscription auth lives in a token file, not an env var."""
        try:
            from ..core.oauth import anthropic as oauth
        except ImportError:
            return CheckResult(
                "llm", "fail", "claude oauth support unavailable",
                hint="reinstall arbor",
            )
        tokens = oauth.load_tokens()
        if tokens is None:
            return CheckResult(
                "llm", "fail",
                "not logged in to Claude (provider=anthropic-oauth)",
                hint="run `arbor login claude`",
            )
        who = tokens.account_email or "account"
        return CheckResult("llm", "pass",
                           f"Claude subscription token found ({who})")

    # ── Check 2: codebase ──────────────────────────────────────────

    def _check_cwd(self) -> CheckResult:
        if not self.cwd.exists():
            return CheckResult("cwd", "fail",
                               f"directory does not exist: {self.cwd}",
                               hint="pass --cwd <existing-dir>")
        visible = [p for p in self.cwd.iterdir() if not p.name.startswith(".")]
        if not visible:
            return CheckResult("cwd", "warn",
                               f"directory is empty: {self.cwd}",
                               hint="add code before starting a research run")
        return CheckResult("cwd", "pass",
                           f"{self.cwd} ({len(visible)} top-level entries)")

    # ── Check 3: git ───────────────────────────────────────────────

    def _check_git(self) -> CheckResult:
        if shutil.which("git") is None:
            return CheckResult("git", "fail", "git is not installed",
                               hint="install git before starting a research run")
        try:
            inside = subprocess.check_output(
                ["git", "rev-parse", "--is-inside-work-tree"],
                cwd=self.cwd, stderr=subprocess.DEVNULL, text=True,
            ).strip()
        except (subprocess.CalledProcessError, OSError):
            inside = "false"
        if inside != "true":
            return CheckResult(
                "git", "warn",
                "not a git repository (the agent uses branches to isolate experiments)",
                hint=f"cd {self.cwd} && git init && git add . && git commit -m init",
            )
        try:
            dirty = subprocess.check_output(
                ["git", "status", "--porcelain"],
                cwd=self.cwd, stderr=subprocess.DEVNULL, text=True,
            ).strip()
        except subprocess.CalledProcessError:
            return CheckResult("git", "warn", "git status failed (corrupt repo?)")
        if dirty:
            n = len(dirty.splitlines())
            return CheckResult(
                "git", "fail",
                f"{n} uncommitted change(s) — repo must be clean before running",
                hint="git add -A && git commit, or git stash",
            )
        return CheckResult("git", "pass", "clean repository")

    # ── Check 4: eval script ───────────────────────────────────────

    def _check_eval(self) -> CheckResult:
        for name in self.EVAL_CANDIDATES:
            if (self.cwd / name).exists():
                return CheckResult("eval", "pass", f"found {name}")
        return CheckResult(
            "eval", "warn",
            f"no eval script found ({', '.join(self.EVAL_CANDIDATES)})",
            hint="create one (a command that prints a numeric score), or rely on the agent to find one",
        )

    # ── Check 5: contamination (declarative, zero-network) ─────────

    def _check_contamination(self) -> CheckResult:
        """Warn when the declared contamination block suggests the test set is
        already in pretraining data. Zero-network — the active probe runs later
        in the coordinator, not here."""
        from ..coordinator.contamination import declarative_assess

        block = (self.eval_contract or {}).get("contamination", {}) or {}
        if not block:
            return CheckResult("contamination", "pass", "no contamination signals declared")
        report = declarative_assess(block, model=None)
        if report.status in {"warn", "contaminated"}:
            return CheckResult(
                "contamination", "warn",
                "; ".join(report.reasons) or "benchmark may be in pretraining data",
                hint="held-out numbers may be inflated — interpret with care",
            )
        return CheckResult("contamination", "pass", "no contamination signals declared")
