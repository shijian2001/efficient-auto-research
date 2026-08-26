"""``arbor idea-check`` — zero-config novelty / prior-art check for one idea.

Runs a single SearchAgent over the public alphaXiv API (no endpoint, no API
key) and prints a lightweight novelty verdict: a summary of what's been done in
the space, the closest related papers, a novelty assessment, and overlap risks.

The agent's verbose ReAct trace is captured behind a live status spinner, so the
user only sees a clean, elegant report. The free-text fields of the report are
written in the same language as the idea.

This is the standalone counterpart to the coordinator's in-loop
``SearchIdeaContext`` annotation — useful for sanity-checking an idea before
ever starting a run.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from pathlib import Path
from typing import Any

import typer

# ── Localization ─────────────────────────────────────────────────────────────

_CJK_RE = re.compile(r"[㐀-鿿豈-﫿぀-ヿ]")


def _detect_lang(text: str) -> str:
    """Crude but dependency-free language pick: Chinese (and CJK) vs English."""
    return "zh" if _CJK_RE.search(text or "") else "en"


_LABELS: dict[str, dict[str, str]] = {
    "en": {
        "title": "Idea Novelty Check",
        "summary": "Summary",
        "papers": "Closest related work",
        "novelty": "Novelty",
        "risks": "Overlap risks",
        "none": "No clearly related papers found.",
        "starting": "Decomposing the idea into search angles…",
        "searching": "Searching alphaXiv…",
        "search_q": "Searching alphaXiv: “{q}”",
        "reading": "Reading {n} candidate paper(s)…",
        "thinking": "Weighing the evidence…  (round {t})",
        "synth": "Writing the novelty verdict…",
    },
    "zh": {
        "title": "想法新颖性审查",
        "summary": "概览",
        "papers": "最接近的相关工作",
        "novelty": "新颖性",
        "risks": "重叠风险",
        "none": "未找到明确相关的论文。",
        "starting": "正在拆解想法、规划检索角度…",
        "searching": "正在检索 alphaXiv…",
        "search_q": "正在检索 alphaXiv：「{q}」",
        "reading": "正在精读 {n} 篇候选论文…",
        "thinking": "正在权衡证据…（第 {t} 轮）",
        "synth": "正在撰写新颖性判定…",
    },
}

_NOVELTY: dict[str, dict[str, tuple[str, str]]] = {
    # value -> lang -> (label, rich-style)
    "novel": {"en": ("NOVEL", "bold green"), "zh": ("新颖", "bold green")},
    "partial-overlap": {
        "en": ("PARTIAL OVERLAP", "bold yellow"),
        "zh": ("部分重叠", "bold yellow"),
    },
    "prior-art-exists": {
        "en": ("PRIOR ART EXISTS", "bold red"),
        "zh": ("已有先行工作", "bold red"),
    },
}


def idea_check_command(
    hypothesis: str = typer.Argument(
        ..., help="The research hypothesis / idea to novelty-check."
    ),
    focus: str | None = typer.Option(
        None, "--focus", help="Optional focus directive (e.g. 'prefer arxiv 2024')."
    ),
    model: str | None = typer.Option(
        None, "--model", help="Override the model used for the check."
    ),
    as_json: bool = typer.Option(
        False, "--json", help="Print the raw SearchAgent JSON instead of a report."
    ),
    cwd: Path | None = typer.Option(
        None, "--cwd", help="Working directory (default: current directory)."
    ),
) -> None:
    """Zero-config novelty / prior-art check for one idea via the alphaXiv public API."""
    from ...coordinator.config import CoordinatorConfig
    from ...coordinator.main import create_provider
    from ...coordinator.tools.search_ctx import _extract_json_block
    from ...search_agent.agent import build_search_agent
    from ...search_agent.prompts import build_search_user_prompt
    from ..user_config import llm_defaults

    lang = _detect_lang(hypothesis)
    labels = _LABELS[lang]

    # Build a CoordinatorConfig from the saved global LLM defaults
    # (~/.arbor/config.yaml: provider / model / base_url / api_key / openai_api),
    # the same source `arbor run` uses, then force the alphaXiv backend on.
    work_dir = os.path.abspath(str(cwd) if cwd is not None else ".")
    llm = llm_defaults()
    kw: dict = {"cwd": work_dir}
    for key in ("provider", "model", "base_url", "api_key", "openai_api"):
        if llm.get(key) is not None:
            kw[key] = llm[key]
    if model:
        kw["model"] = model
    try:
        config = CoordinatorConfig(**kw)
    except Exception as exc:  # noqa: BLE001
        typer.secho(f"error: could not build config: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    config.search.enabled = True
    config.search.builtin_backend = "alphaxiv"

    try:
        provider = create_provider(config)
    except Exception as exc:  # noqa: BLE001
        typer.secho(
            f"error: could not create an LLM provider — check your credentials "
            f"(run `arbor login` or `arbor setup`): {exc}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1) from exc

    user_prompt = build_search_user_prompt(
        hypothesis=hypothesis,
        focus=focus,
        report_language="the same language as the hypothesis above",
    )

    async def _run() -> str:
        agent = build_search_agent(
            provider=provider,
            search_config=config.search,
            cwd=work_dir,
            meta_config=config,
            context_window=config.context_window,
        )
        return await agent.run(user_prompt)

    raw = _run_with_status(_run, labels)
    if raw is None:
        raise typer.Exit(code=1)

    parsed = _extract_json_block(raw)
    if as_json or parsed is None:
        if parsed is None and not as_json:
            typer.secho(
                "warning: could not parse a JSON verdict — showing raw output.",
                fg=typer.colors.YELLOW,
                err=True,
            )
        typer.echo(raw.strip())
        return

    _render_report(parsed, hypothesis=hypothesis, lang=lang)


def _run_with_status(run_coro, labels: dict[str, str]) -> str | None:
    """Run the agent while routing its verbose ReAct trace into a single live
    status line (on stderr) that reflects real progress — the actual query
    being searched, how many papers are being read, the reasoning round — plus
    a ticking elapsed timer so a long LLM call never looks frozen. Returns the
    raw agent output, or None on error."""
    from rich.console import Console

    from ...core import agent as agent_mod

    err_console = Console(stderr=True)
    # Mutable shared state the hook writes and the ticker reads.
    state = {"phase": labels["starting"], "turn": 0, "done_search": False}

    def _hook(event: str, payload: dict[str, Any]) -> None:
        try:
            if event == "tool_call":
                name = payload.get("name", "")
                inputs = payload.get("inputs", {}) or {}
                if name == "web_search":
                    q = _first_query(inputs.get("query"))
                    state["phase"] = (
                        labels["search_q"].format(q=q) if q else labels["searching"]
                    )
                elif name == "web_visit":
                    url = inputs.get("url")
                    n = len(url) if isinstance(url, (list, tuple)) else 1
                    state["phase"] = labels["reading"].format(n=n)
                    state["done_search"] = True
            elif event == "status":
                msg = str(payload.get("message", ""))
                if "calling" in msg:
                    state["turn"] += 1
                    # After the agent has searched + read, the last turns are
                    # synthesis; surface that instead of a generic "thinking".
                    state["phase"] = (
                        labels["synth"]
                        if state["done_search"] and state["turn"] >= 3
                        else labels["thinking"].format(t=state["turn"])
                    )
        except Exception:  # noqa: BLE001 - never let display break the run
            pass

    async def _driver() -> str:
        start = time.monotonic()

        async def _ticker() -> None:
            while True:
                elapsed = int(time.monotonic() - start)
                _status.update(
                    f"[cyan]{state['phase']}[/cyan]  [dim]· {elapsed}s[/dim]"
                )
                await asyncio.sleep(0.4)

        tick = asyncio.create_task(_ticker())
        try:
            return await run_coro()
        finally:
            tick.cancel()
            try:
                await tick
            except asyncio.CancelledError:
                pass

    prev_hook = getattr(agent_mod, "DISPLAY_HOOK", None)
    with err_console.status(
        f"[cyan]{labels['starting']}[/cyan]", spinner="dots"
    ) as _status:
        agent_mod.DISPLAY_HOOK = _hook
        try:
            return asyncio.run(_driver())
        except Exception as exc:  # noqa: BLE001
            err_console.print(f"[red]error: idea check failed: {exc}[/red]")
            return None
        finally:
            agent_mod.DISPLAY_HOOK = prev_hook


def _first_query(query: Any) -> str:
    """Pull the first query string out of a web_search ``query`` arg and clip it."""
    q = ""
    if isinstance(query, (list, tuple)) and query:
        q = str(query[0])
    elif isinstance(query, str):
        q = query
    q = q.strip()
    return (q[:44] + "…") if len(q) > 45 else q


def _render_report(parsed: dict[str, Any], *, hypothesis: str, lang: str) -> None:
    """Render the verdict as a single elegant panel on stdout."""
    try:
        from rich.console import Console, Group
        from rich.panel import Panel
        from rich.text import Text
    except Exception:  # noqa: BLE001 - rich is a core dep; degrade gracefully
        _render_plain(parsed, hypothesis=hypothesis, lang=lang)
        return

    labels = _LABELS[lang]
    console = Console()

    summary = str(parsed.get("summary", "")).strip()
    novelty = str(parsed.get("novelty_assessment", "")).strip()
    overlap = str(parsed.get("overlap_risks", "")).strip()
    papers = parsed.get("related_papers") or []

    blocks: list[Any] = []

    # Novelty badge.
    label, style = _NOVELTY.get(novelty, {}).get(
        lang, (novelty.upper() or "—", "bold")
    )
    verdict = Text()
    verdict.append(f"{labels['novelty']}:  ", style="bold")
    verdict.append(f" {label} ", style=f"reverse {style}")
    blocks.append(verdict)

    if summary:
        blocks.append(Text())
        blocks.append(Text(labels["summary"], style="bold cyan"))
        blocks.append(Text(summary))

    blocks.append(Text())
    blocks.append(Text(labels["papers"], style="bold cyan"))
    if isinstance(papers, list) and papers:
        for i, p in enumerate(papers, 1):
            if not isinstance(p, dict):
                continue
            title = str(p.get("title", "")).strip() or "(untitled)"
            url = str(p.get("url", "")).strip()
            rel = str(p.get("one_line_relevance", "")).strip()
            line = Text(f" {i}. ", style="bold")
            line.append(title, style=f"link {url}" if url else "")
            blocks.append(line)
            if url:
                blocks.append(Text(f"    {url}", style="dim blue"))
            if rel:
                blocks.append(Text(f"    {rel}", style="italic"))
    else:
        blocks.append(Text(f" {labels['none']}", style="dim"))

    if overlap:
        blocks.append(Text())
        blocks.append(Text(labels["risks"], style="bold cyan"))
        blocks.append(Text(overlap))

    subtitle = hypothesis.strip()
    if len(subtitle) > 88:
        subtitle = subtitle[:87] + "…"

    console.print()
    console.print(
        Panel(
            Group(*blocks),
            title=f"🔎 {labels['title']}",
            subtitle=f"[dim]{subtitle}[/dim]",
            border_style="cyan",
            padding=(1, 2),
        )
    )


def _render_plain(parsed: dict[str, Any], *, hypothesis: str, lang: str) -> None:
    """Plaintext fallback if rich is unavailable."""
    labels = _LABELS[lang]
    novelty = str(parsed.get("novelty_assessment", "")).strip()
    label = _NOVELTY.get(novelty, {}).get(lang, (novelty, ""))[0]
    out: list[str] = [f"== {labels['title']} ==", f"{labels['novelty']}: {label}"]
    if parsed.get("summary"):
        out += ["", labels["summary"], str(parsed["summary"]).strip()]
    out += ["", labels["papers"]]
    papers = parsed.get("related_papers") or []
    if papers:
        for i, p in enumerate(papers, 1):
            if isinstance(p, dict):
                out.append(f" {i}. {p.get('title', '')} — {p.get('url', '')}")
                if p.get("one_line_relevance"):
                    out.append(f"    {p['one_line_relevance']}")
    else:
        out.append(f" {labels['none']}")
    if parsed.get("overlap_risks"):
        out += ["", labels["risks"], str(parsed["overlap_risks"]).strip()]
    typer.echo("\n".join(out))
