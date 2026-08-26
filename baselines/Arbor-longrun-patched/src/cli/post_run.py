"""Post-run terminal surfaces: rendered report + follow-up chat."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Iterable

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.styles import Style
from rich.markdown import Markdown
from rich.markup import escape
from rich.padding import Padding
from rich.text import Text

from ..core.agent import Agent
from ..core.config import AgentConfig
from ..core.llm.base import LLMProvider
from ..core.tools.file_read import FileReadTool
from ..core.tools.glob_tool import GlobTool
from ..core.tools.grep import GrepTool
from .intake.display import IntakeDisplay
from .style import console


POST_RUN_COMMANDS: list[tuple[str, str]] = [
    ("/help", "show available commands"),
    ("/status", "show run status"),
    ("/quit", "exit arbor"),
    ("/report", "show the final report again"),
    ("/tree", "show idea tree path"),
    ("/cost", "show token usage"),
    ("/paths", "show report and artifact paths"),
    ("/reset", "clear follow-up chat history"),
]


def render_final_report(report_text: str, *, report_path: Path | None = None) -> None:
    """Render a report as terminal UI instead of dumping raw markdown."""
    if not report_text.strip():
        return

    title = Text("final report", style="bold green")
    if report_path is not None:
        title.append(f"  ·  {report_path.name}", style="dim")

    console.print()
    console.rule(title, style="green")
    console.print(Padding(
        Markdown(report_text, code_theme="monokai", inline_code_theme="monokai"),
        (1, 2),
    ))
    if report_path is not None:
        console.print(f"[dim]saved to {escape(str(report_path))}[/dim]")
    console.rule(style="green")
    console.print()


async def run_post_run_repl(
    *,
    provider: LLMProvider,
    project_cwd: Path,
    session_dir: Path,
    report_path: Path | None,
    instruction: str,
    model: str,
    enabled: bool = True,
) -> None:
    """Let the user ask questions after the run has finished.

    This mode is intentionally read-only. It can inspect run artifacts and the
    target project, but it does not launch another experiment or edit files.
    """
    if not enabled or not _interactive_stdin():
        return

    session_dir = session_dir.resolve()
    project_cwd = project_cwd.resolve()
    workspace = session_dir / "_followup"
    workspace.mkdir(parents=True, exist_ok=True)

    tools = [
        FileReadTool(cwd=str(project_cwd), workspace_dir=str(workspace)),
        GlobTool(cwd=str(project_cwd), workspace_dir=str(workspace)),
        GrepTool(cwd=str(project_cwd), workspace_dir=str(workspace)),
    ]
    agent = Agent(
        provider=provider,
        tools=tools,
        system_prompt=_build_followup_prompt(
            project_cwd=project_cwd,
            session_dir=session_dir,
            report_path=report_path,
            instruction=instruction,
        ),
        config=AgentConfig(
            cwd=str(project_cwd),
            provider=provider.__class__.__name__.replace("Provider", "").lower(),
            model=model,
            max_turns=12,
            max_tokens=8192,
            max_tool_concurrency=4,
            auto_git=False,
            workspace_dir=str(workspace),
        ),
    )

    _print_followup_banner(report_path=report_path)
    session = _build_session()
    transcript_path = session_dir / "conversation.md"

    while True:
        try:
            user_text = (await session.prompt_async(_prompt())).strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]leaving follow-up[/dim]")
            return
        if not user_text:
            continue
        if user_text.startswith("/"):
            action = _handle_command(
                user_text,
                agent=agent,
                report_path=report_path,
                session_dir=session_dir,
            )
            if action == "quit":
                return
            continue
        _append_transcript(transcript_path, "User", user_text)
        with IntakeDisplay(console=console):
            reply = await agent.run(user_text)
        _append_transcript(transcript_path, "Agent", reply)


def _build_followup_prompt(
    *,
    project_cwd: Path,
    session_dir: Path,
    report_path: Path | None,
    instruction: str,
) -> str:
    report_line = str(report_path) if report_path is not None else "(report generation failed)"
    return f"""You are Arbor's post-run assistant.

The research run has already finished. Help the user inspect and understand
the completed run. Answer in the same language the user uses.

Context:
- Project directory: {project_cwd}
- Session directory: {session_dir}
- Final report: {report_line}
- Original instruction: {instruction or '(not provided)'}

Important behavior:
- For questions about results, decisions, scores, ideas, or artifacts, read
  the final report path first when it exists. Then inspect idea_tree.json,
  idea_tree.md, run_stats.json, events.jsonl, or project files if the report
  is insufficient.
- This follow-up mode is read-only. You cannot start a new experiment, edit
  files, or run shell commands here.
- If the user wants to continue work, give a concrete next `arbor run ...`
  command and a refined instruction, but do not claim another run has started.
- Keep answers concise unless the user asks for detail. Use Markdown when it
  improves readability.
"""


def _print_followup_banner(*, report_path: Path | None) -> None:
    console.print(
        "[bold magenta]follow-up[/bold magenta] "
        "[dim]Ask about the run, or type /quit to exit.[/dim]"
    )
    if report_path is not None:
        console.print(f"[dim]Use /report to show {escape(str(report_path))} again.[/dim]")
    console.print()


def _prompt() -> ANSI:
    return ANSI("\033[1;35mfollow-up\033[0m \033[2m›\033[0m ")


def _interactive_stdin() -> bool:
    return bool(sys.stdin and sys.stdin.isatty())


def _append_transcript(path: Path, speaker: str, text: str) -> None:
    if not text.strip():
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with path.open("a", encoding="utf-8") as f:
            f.write(f"\n\n## {speaker} · {stamp}\n\n")
            f.write(text.strip())
            f.write("\n")
    except OSError:
        return


class _SlashCompleter(Completer):
    def __init__(self, commands: Iterable[tuple[str, str]]) -> None:
        self._commands = list(commands)
        self._name_width = max(len(name) for name, _ in self._commands) if self._commands else 0

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if not text.startswith("/"):
            return
        for name, desc in self._commands:
            if name.startswith(text):
                yield Completion(
                    name,
                    start_position=-len(text),
                    display=f"  {name:<{self._name_width}}  ",
                    display_meta=desc,
                )


def _build_session() -> PromptSession:
    return PromptSession(
        history=InMemoryHistory(),
        completer=_SlashCompleter(POST_RUN_COMMANDS),
        complete_while_typing=True,
        style=Style.from_dict({
            "completion-menu": "bg:#1c1c1c",
            "completion-menu.completion": "bg:#1c1c1c fg:#d75fff",
            "completion-menu.meta.completion": "bg:#1c1c1c fg:#808080",
            "completion-menu.completion.current": "bg:#af00d7 fg:#ffffff bold",
            "completion-menu.meta.completion.current": "bg:#af00d7 fg:#ffd7ff",
            "scrollbar.background": "bg:#1c1c1c",
            "scrollbar.button": "bg:#d75fff",
        }),
    )


def _handle_command(
    line: str,
    *,
    agent: Agent,
    report_path: Path | None,
    session_dir: Path,
) -> str:
    cmd = line.lower().split()[0]
    if cmd == "/help":
        console.print("[bold]commands[/bold] [dim](type / to bring up the menu)[/dim]")
        for name, desc in POST_RUN_COMMANDS:
            console.print(f"  [magenta]{name:<8}[/magenta] [dim]{desc}[/dim]")
    elif cmd == "/quit":
        return "quit"
    elif cmd == "/reset":
        agent.messages.clear()
        console.print("[dim]follow-up history cleared[/dim]")
    elif cmd == "/status":
        stats = _load_json(session_dir / "run_stats.json")
        iterations = stats.get("iterations", {}) if isinstance(stats, dict) else {}
        console.print("[bold magenta]run status[/bold magenta]")
        console.print(f"  [dim]session[/dim] {escape(str(session_dir))}")
        if stats.get("duration_human"):
            console.print(f"  [dim]duration[/dim] {escape(str(stats['duration_human']))}")
        if stats.get("model"):
            console.print(f"  [dim]model[/dim] {escape(str(stats['model']))}")
        if iterations:
            console.print(f"  [dim]best[/dim] {escape(str(iterations.get('best_score', '—')))}")
            console.print(f"  [dim]trunk[/dim] {escape(str(iterations.get('trunk_score', '—')))}")
    elif cmd == "/report":
        if report_path is None or not report_path.exists():
            console.print("[yellow]report file is not available[/yellow]")
        else:
            try:
                render_final_report(report_path.read_text(encoding="utf-8"), report_path=report_path)
            except OSError as exc:
                console.print(f"[yellow]could not read report: {escape(repr(exc))}[/yellow]")
    elif cmd == "/tree":
        tree_md = session_dir / ".coordinator" / "idea_tree.md"
        tree_json = session_dir / ".coordinator" / "idea_tree.json"
        if tree_md.exists():
            console.print(f"  [dim]idea tree[/dim] {escape(str(tree_md))}")
        elif tree_json.exists():
            console.print(f"  [dim]idea tree[/dim] {escape(str(tree_json))}")
        else:
            console.print("[yellow]idea tree is not available[/yellow]")
    elif cmd == "/cost":
        stats = _load_json(session_dir / "run_stats.json")
        all_agents = stats.get("all_agents", {}) if isinstance(stats, dict) else {}
        console.print("[bold magenta]cost[/bold magenta]")
        console.print(f"  [dim]LLM calls[/dim] {escape(str(all_agents.get('total_llm_calls', '—')))}")
        console.print(f"  [dim]tokens in[/dim] {escape(str(all_agents.get('total_input_tokens', '—')))}")
        if all_agents.get("total_uncached_input_tokens") is not None:
            console.print(
                f"  [dim]tokens in uncached[/dim] "
                f"{escape(str(all_agents.get('total_uncached_input_tokens', '—')))}"
            )
        if all_agents.get("total_cache_read_tokens"):
            console.print(
                f"  [dim]tokens cache read[/dim] "
                f"{escape(str(all_agents.get('total_cache_read_tokens', '—')))}"
            )
        if all_agents.get("total_cache_creation_tokens"):
            console.print(
                f"  [dim]tokens cache create[/dim] "
                f"{escape(str(all_agents.get('total_cache_creation_tokens', '—')))}"
            )
        console.print(f"  [dim]tokens out[/dim] {escape(str(all_agents.get('total_output_tokens', '—')))}")
        console.print(f"  [dim]tokens total[/dim] {escape(str(all_agents.get('total_tokens', '—')))}")
    elif cmd == "/paths":
        paths = [
            ("session", session_dir),
            ("report", report_path),
            ("idea tree", session_dir / ".coordinator" / "idea_tree.json"),
            ("events", session_dir / "events.jsonl"),
            ("transcript", session_dir / "conversation.md"),
        ]
        for label, path in paths:
            if path is not None:
                console.print(f"  [dim]{label:<10}[/dim] {escape(str(path))}")
    else:
        console.print(f"[yellow]unknown command: {escape(cmd)} (try /help)[/yellow]")
    return "continue"


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
