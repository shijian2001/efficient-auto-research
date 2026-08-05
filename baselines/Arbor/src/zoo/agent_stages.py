"""Agent-driven collection stages (Stage 2: baseline bring-up).

The collection *spine* (:mod:`arbor.zoo.collect`) is deterministic — it acquires
materials and scaffolds a draft. This module is the agent-driven part: given a live
LLM provider, :func:`bringup` spawns one agent in the draft folder to make a baseline
actually run — install deps, get the reference working, wrap a clean ``eval`` that
prints ``score:``, and write the README + PROVENANCE — then checks its work by running
the eval and the structural verifier.

It reuses the core :class:`~arbor.core.agent.Agent` runtime (the same one the executor
uses) but stays a standalone flow: it never wires into the Coordinator/Executor research
loop (a §2.1 correctness requirement).

The agent run is behind an injected ``run_agent`` callable so the orchestration is
testable without a live LLM (a fake runner writes the files a real agent would). The real
runner — :func:`real_agent_runner` — constructs the ``Agent`` with bash + file tools and
needs a configured provider (API key); validating its *reasoning* needs live iteration.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Protocol

from .pack import find_eval_entrypoint
from .verify import VerifyResult, verify_pack


class AgentRunner(Protocol):
    """Runs an agent to completion in *cwd* and returns its final transcript text."""

    def __call__(self, *, cwd: Path, system_prompt: str, task: str,
                 max_turns: int) -> Awaitable[str]: ...


BRINGUP_SYSTEM_PROMPT = """\
You are a benchmark bring-up assistant. You turn acquired materials (a research repo, a
dataset) into a runnable Arbor benchmark in the current directory. You write the *measurement
plumbing and a working baseline* — never an optimized solution.

Produce, in the current directory:
  * a runnable eval: `bash eval.sh dev|test` (or `python eval.py --split dev|test`) prints
    exactly one line `score: <float>`, after a correctness check. dev and test must use
    DISJOINT data (the held-out split).
  * the editable baseline (e.g. `solution.py`) — the simplest correct reference, the thing
    Arbor will later optimize. Do NOT optimize it.
  * `README.md` — plain language: the task, the metric (and whether higher/lower is better),
    which file(s) Arbor may edit, and how dev/test differ.
  * `PROVENANCE.md` — for humans: Source, Setup & environment, Baseline, Contamination
    assessment, Caveats.

The "baseline" is the *starting point Arbor will optimize*, NOT a SOTA method. It may come
from three places — follow the baseline plan you are given, and use `AskUser` if it is unclear:
  * harvest   — take a simple runnable baseline already in the repo (direct generation,
    naive RAG, an earlier system) rather than the repo's headline method;
  * implement — write a baseline to the user's described method/instruction (you are given it
    below as the user's request);
  * web       — find an existing baseline implementation online and adapt it.

Use the acquired source materials at the path you are told. Install dependencies as needed.
You are DONE when the four artifacts exist, `arbor benchmark verify .` would pass, and the
eval is *runnable*. Do NOT block on actually running it to completion: a real run may need a
served model, an API key, or a search key the user has not set up. If you can run the eval
cheaply (e.g. CPU-only), do so to sanity-check it; otherwise make it runnable, document the
exact setup needed in README + PROVENANCE, and stop — leaving a runnable draft is success.
If you are blocked on something you cannot resolve, write what you have and explain it clearly.
"""


@dataclass
class BringupResult:
    """Outcome of a bring-up run."""

    transcript: str = ""
    dev_score: float | None = None
    ran: bool = False                      # the eval actually ran and produced a score
    verify: list[VerifyResult] = field(default_factory=list)
    ok: bool = False
    notes: list[str] = field(default_factory=list)


def _parse_score(text: str) -> float | None:
    # Mirrors arbor.mcp.session_ops.parse_score for the documented `score: <float>` line,
    # kept local so arbor.zoo stays dependency-light.
    import re
    matches = re.findall(r"\bscore\s*[:=]\s*([-+]?\d+(?:\.\d+)?)", text, re.I)
    return float(matches[-1]) if matches else None


def _run_eval_dev(pack_dir: Path, timeout: int) -> tuple[float | None, str]:
    """Run the eval on the dev split and parse a score (the bring-up success check)."""
    entry = find_eval_entrypoint(pack_dir)
    if entry == "eval.sh":
        cmd = ["bash", str(pack_dir / "eval.sh"), "dev"]
    elif entry == "eval.py":
        cmd = [os.environ.get("PYTHON", "python3"), str(pack_dir / "eval.py"), "--split", "dev"]
    else:
        return None, "no eval entrypoint"
    try:
        proc = subprocess.run(cmd, cwd=str(pack_dir), capture_output=True, text=True,
                              timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, f"eval timed out after {timeout}s"
    out = proc.stdout + proc.stderr
    return _parse_score(out), out[-2000:]


async def bringup(
    pack_dir: Path,
    *,
    run_agent: AgentRunner,
    materials_dir: Path | None = None,
    instruction: str = "",
    baseline_plan: dict[str, Any] | None = None,
    max_turns: int = 40,
    eval_timeout: int = 600,
) -> BringupResult:
    """Run the bring-up agent in *pack_dir*, then check its work.

    *run_agent* does the actual agent work (real: an :class:`Agent`; in tests: a fake that
    writes the files). *materials_dir* is the acquired source the agent should draw from.
    *instruction* is the user's original natural-language request (so a baseline can be
    *implemented* to their described method), and *baseline_plan* records where the baseline
    should come from (harvest / implement / web).

    Success is a **runnable draft**: the artifacts are present and the structural verify
    passes. Actually running the eval is best-effort — a real run may need a served model /
    API key the user hasn't set up — so a non-running eval is noted, not a failure.
    """
    result = BringupResult()
    parts = [f"Bring up the benchmark in this directory ({pack_dir}). Produce a runnable "
             f"baseline and an eval that prints a `score:` line on dev and test, plus "
             f"README.md + PROVENANCE.md."]
    if materials_dir:
        parts.append(f"The acquired source materials are at: {materials_dir}")
    if instruction:
        parts.append(f"The user's original request (use it to shape/implement the baseline):\n"
                     f"{instruction}")
    if baseline_plan:
        src = baseline_plan.get("source", "?")
        detail = baseline_plan.get("detail", "")
        parts.append(f"Baseline plan — source={src}: {detail}")
    task = "\n\n".join(parts)
    try:
        result.transcript = await run_agent(
            cwd=pack_dir, system_prompt=BRINGUP_SYSTEM_PROMPT, task=task, max_turns=max_turns)
    except Exception as exc:  # noqa: BLE001 — surface agent/provider errors as a blocker
        result.notes.append(f"agent run failed: {exc}")
        return result

    # ── success check: the pack verifies (a runnable draft); running the eval is best-effort ──
    result.dev_score, eval_out = _run_eval_dev(pack_dir, eval_timeout)
    result.ran = result.dev_score is not None
    if not result.ran:
        result.notes.append(
            "eval did not produce a score here — left as a runnable draft (a real run may "
            f"need a served model / API key):\n{eval_out}")
    result.verify = verify_pack(pack_dir)
    verify_ok = not any(r.status == "fail" for r in result.verify)
    result.ok = verify_ok
    if not verify_ok:
        result.notes.append("structural verify still has failures")
    return result


# ── Stage 0: discovery (natural-language query → a chosen benchmark source) ──

DISCOVERY_SYSTEM_PROMPT = """\
You are a benchmark discovery assistant. Given a natural-language request, you search the
web for a benchmark that fits, judge the candidates, and pick the single best one.

Use the search and page-fetch tools to look across GitHub, HuggingFace, arXiv /
PapersWithCode, and leaderboards. For each candidate, judge:
  * does it ship a runnable eval and a baseline (not just a dataset)?
  * does the task have headroom to optimize (an artifact Arbor can edit), not just measure
    a frozen model?
  * compute fit and license — can it be cloned and run, and is it redistributable?
  * is it a representative / actively-used benchmark for the request?

Prefer a GitHub repo that already contains an eval + baseline. **Be efficient: as soon as
you have identified one suitable repo and can name its benchmark(s) and baseline(s) — usually
after reading the paper and the repo README — STOP and output your choice. Do not
exhaustively clone and grep.**

The "baseline" is the *starting point Arbor will optimize*, NOT the repo's headline method.
A repo that proposes a method usually also ships simpler baselines (direct generation, naive
RAG, an earlier system); for Arbor those simpler baselines are the harvestable ones, because
they leave headroom to optimize. Name a concrete baseline implementation (a runnable script),
not a published number. When which one to treat as the baseline is genuinely the user's call
and an `AskUser` tool is available, ask them rather than guessing.

The request may name a *work* that uses several datasets/benchmarks (e.g. "get me the
datasets in WebThinker"). In that case enumerate the datasets it uses and, if an `AskUser`
tool is available, ask the user **which single dataset** they want — then resolve that one.
Also decide where the baseline will come from and record it as `baseline_plan.source`:
  * "harvest"   — a runnable baseline script already in the repo,
  * "implement" — write one to the user's described method/instruction (e.g. "design xxx"),
  * "web"       — find an existing implementation online.
Ask the user (AskUser) when the baseline source is genuinely their call.

End your reply with a single fenced JSON block describing your choice (and nothing after it):

```json
{
  "name": "short_kebab_name",
  "source": {"kind": "git", "url": "https://github.com/owner/repo"},
  "metric": "what is optimized, and whether higher/lower is better",
  "baseline": "where/what the harvestable baseline is",
  "baseline_plan": {"source": "harvest", "detail": "which script / method / search to use"},
  "why": "one or two sentences on why this fits the request"
}
```

If nothing suitable is found, set "source" to null and explain in "why".
"""


@dataclass
class DiscoveryResult:
    """Outcome of a discovery run: the chosen benchmark source (or none)."""

    choice: dict[str, Any] | None = None     # {name, source:{kind,url}, metric, baseline, why}
    transcript: str = ""
    ok: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def url(self) -> str | None:
        src = (self.choice or {}).get("source") or {}
        return src.get("url") if isinstance(src, dict) else None

    @property
    def name(self) -> str | None:
        return (self.choice or {}).get("name")

    @property
    def baseline_plan(self) -> dict[str, Any]:
        """How/where the baseline should come from: {source: harvest|implement|web, detail}."""
        plan = (self.choice or {}).get("baseline_plan")
        return plan if isinstance(plan, dict) else {}


def _extract_json(text: str) -> dict[str, Any] | None:
    """Pull the last JSON object out of the agent's reply (a ```json fenced block, or a
    bare top-level object)."""
    blocks = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if not blocks:
        blocks = re.findall(r"(\{(?:[^{}]|\{[^{}]*\})*\})", text, re.DOTALL)
    for block in reversed(blocks):
        try:
            obj = json.loads(block)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and "source" in obj:
            return obj
    return None


async def discover(
    query: str,
    *,
    run_agent: AgentRunner,
    work_dir: Path,
    max_turns: int = 100,
) -> DiscoveryResult:
    """Run the discovery agent on a natural-language *query*; return the chosen source.

    *run_agent* should be a search-enabled runner (``real_agent_runner(with_search=True)``);
    *work_dir* is a scratch directory for the agent's tools.
    """
    result = DiscoveryResult()
    work_dir.mkdir(parents=True, exist_ok=True)
    task = (
        f"Find a benchmark for this request and pick the single best one:\n\n{query}\n\n"
        "Search across GitHub / HuggingFace / arXiv, judge the candidates, and end with the "
        "JSON block described in your instructions."
    )
    try:
        result.transcript = await run_agent(
            cwd=work_dir, system_prompt=DISCOVERY_SYSTEM_PROMPT, task=task, max_turns=max_turns)
    except Exception as exc:  # noqa: BLE001 — surface agent/provider errors
        result.notes.append(f"agent run failed: {exc}")
        return result

    choice = _extract_json(result.transcript)
    if choice is None:
        result.notes.append("no JSON choice found in the agent's reply")
        return result
    result.choice = choice
    if not result.url:
        result.notes.append(f"no source url chosen: {choice.get('why', '(no reason given)')}")
        return result
    result.ok = True
    return result


def real_agent_runner(
    *,
    with_search: bool = False,
    ask_user: bool = False,
    provider: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
) -> AgentRunner:
    """Build the real :class:`Agent`-backed runner. Needs a configured provider / API key.

    With ``with_search=True`` the agent also gets keyless web search + fetch tools
    (alphaXiv + Jina) so it can browse for benchmarks. With ``ask_user=True`` it gets a
    console-backed :class:`~arbor.zoo.ask_tool.ConsoleAskUserTool` so it can put a genuinely
    human decision (e.g. which implementation is the baseline) to the user at the terminal —
    only enable this when stdin is interactive. Heavy imports are deferred so importing
    :mod:`arbor.zoo` stays light.
    """
    async def _run(*, cwd: Path, system_prompt: str, task: str, max_turns: int) -> str:
        from arbor.core import Agent, AgentConfig, create_provider
        from arbor.core.tools import get_all_tools

        # Only pass provider fields that were set, so AgentConfig's defaults (which read
        # the env / user config) apply when they're omitted.
        llm_kw = {k: v for k, v in
                  {"provider": provider, "model": model, "api_key": api_key,
                   "base_url": base_url}.items() if v is not None}
        cfg = AgentConfig(cwd=str(cwd), max_turns=max_turns, auto_git=False, **llm_kw)
        prov = create_provider(cfg)
        tools = list(get_all_tools(cwd=str(cwd), config=cfg))
        if ask_user:
            from .ask_tool import ConsoleAskUserTool
            tools.append(ConsoleAskUserTool(cwd=str(cwd)))
        if with_search:
            from arbor.coordinator.config import SearchConfig
            from arbor.core.tools.web.factory import (
                build_web_search_tool,
                build_web_visit_tool,
            )
            sc = SearchConfig(builtin_backend="alphaxiv", visit_backend="jina")
            for t in (build_web_search_tool(sc, cwd=str(cwd)),
                      build_web_visit_tool(sc, cwd=str(cwd))):
                if t is not None:
                    tools.append(t)
        agent = Agent(provider=prov, tools=tools, system_prompt=system_prompt, config=cfg)
        return await agent.run(task)

    return _run
