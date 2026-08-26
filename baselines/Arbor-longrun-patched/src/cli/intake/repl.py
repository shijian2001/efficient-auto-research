"""Interactive REPL that drives the intake agent."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Iterable

import typer
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.styles import Style
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from ..._app import CONFIG_DIR_NAME
from .._constants import (
    INTAKE_LLM_RETRY_ATTEMPTS,
    INTAKE_LLM_RETRY_BASE_DELAY,
    INTAKE_LLM_RETRY_MAX_DELAY,
)
from ...core.agent import Agent
from ...core.config import AgentConfig
from ...core.llm.base import LLMProvider
from ...core.tools.base import Tool
from ...core.tools.file_read import FileReadTool
from ...core.tools.glob_tool import GlobTool
from ...core.tools.grep import GrepTool
from ...coordinator.checkpoint import seal_interrupted_tail
from .conversation_store import (
    ConversationRecord,
    find_conversations,
    latest_unfinished,
    load_messages,
    new_conversation,
    save_conversation,
)
from .display import IntakeDisplay
from .launch_tool import LaunchExperimentTool, LaunchPlan, LaunchState
from .scope import (
    IntakeMode,
    IntakePathPolicy,
    infer_intake_mode,
    is_explicit_launch_approval,
)
from .system_prompt import build_discussion_system_prompt, build_system_prompt
from ..resume_picker import ResumableSession


_console = Console()
log = logging.getLogger(__name__)


# Single source of truth for slash commands.
# (name, description) — the completer renders these, _handle_slash dispatches.
SLASH_COMMANDS: list[tuple[str, str]] = [
    ("/help",  "show available commands"),
    ("/resume", "resume a past conversation or run"),
    ("/plugin", "select plugin: /plugin <name> [profile]"),
    ("/skill", "manage skills: /skill load|unload <name...>"),
    ("/status", "show intake status"),
    ("/quit",  "exit without launching"),
    ("/abort", "exit without launching"),
    ("/reset", "clear chat history (keep session)"),
    ("/tools", "list the agent's available tools"),
    ("/plan",  "show the agent's pending plan, if any"),
    ("/contract", "show the pending Research Contract"),
]


async def run_intake(
    *,
    provider: LLMProvider,
    starting_cwd: Path,
    seed_message: str | None,
    workspace_dir: Path | None = None,
    intake_max_turns: int = 30,
    continue_latest: bool = False,
) -> LaunchPlan | ResumableSession | None:
    """Drive the intake REPL. Returns the launch outcome:

    - ``LaunchPlan``       — the user described a goal; start a fresh run
    - ``ResumableSession`` — the user ran ``/resume`` and picked a past run
    - ``None``             — the user aborted

    `starting_cwd` is the directory the user invoked the CLI from. It is the
    default project boundary until the user explicitly names another target.
    The intake workspace dir (tool persistence, etc.) lives next to it.

    The conversation itself is auto-saved every turn under
    ``<starting_cwd>/.arbor/conversations/`` so it can be continued later. With
    ``continue_latest`` (``arbor --continue``) the newest unfinished
    conversation there is reloaded and the chat picks up where it left off.
    """
    starting_cwd = starting_cwd.resolve()

    state = LaunchState()
    intake_workspace = (workspace_dir or (starting_cwd / CONFIG_DIR_NAME / "_intake")).resolve()
    intake_workspace.mkdir(parents=True, exist_ok=True)

    path_policy = IntakePathPolicy(starting_cwd)

    # Intake is read-only. Every file tool shares a mutable, canonical path
    # policy, so a user correction changes the enforced scope before the next
    # model call. Shell access belongs to the launched coordinator, not to a
    # planning/discussion chat.
    file_tools: list[Tool] = [
        FileReadTool(
            cwd=str(starting_cwd),
            workspace_dir=str(intake_workspace),
            path_authorizer=path_policy.authorize,
            persist_results=False,
        ),
        GlobTool(
            cwd=str(starting_cwd),
            workspace_dir=str(intake_workspace),
            path_authorizer=path_policy.authorize,
            persist_results=False,
        ),
        GrepTool(
            cwd=str(starting_cwd),
            workspace_dir=str(intake_workspace),
            path_authorizer=path_policy.authorize,
            persist_results=False,
        ),
    ]
    launch_tool = LaunchExperimentTool(
        cwd=str(starting_cwd),
        workspace_dir=str(intake_workspace),
        state=state,
        path_authorizer=path_policy.authorize,
    )
    tools: list[Tool] = [*file_tools, launch_tool]

    agent_config = AgentConfig(
        cwd=str(starting_cwd),
        provider=_provider_label(provider),
        model=getattr(provider, "model", "unknown"),
        max_turns=intake_max_turns,
        yield_on_text=True,
        premature_stop_nudges=False,
        llm_retry_attempts=INTAKE_LLM_RETRY_ATTEMPTS,
        llm_retry_base_delay=INTAKE_LLM_RETRY_BASE_DELAY,
        llm_retry_max_delay=INTAKE_LLM_RETRY_MAX_DELAY,
        auto_git=False,  # intake exposes no write/shell tools
    )

    agent = Agent(
        provider=provider,
        tools=tools,
        system_prompt=build_system_prompt(
            starting_cwd=str(starting_cwd),
            approved_scope=path_policy.describe(),
        ),
        config=agent_config,
    )

    _print_welcome(
        seed_message,
        starting_cwd=starting_cwd,
        model=getattr(provider, "model", None),
        base_url=getattr(provider, "base_url", None),
    )

    session = _build_session(starting_cwd, state=state)

    # The conversation is auto-saved every turn so it can be resumed. On
    # --continue, reload the newest unfinished conversation and keep chatting;
    # otherwise start a fresh record (written lazily on the first save).
    conv: ConversationRecord = new_conversation(starting_cwd)
    if continue_latest:
        prior = latest_unfinished(starting_cwd)
        if prior is not None:
            agent.messages = seal_interrupted_tail(load_messages(prior))
            # Conversation prose is resumable; filesystem authority is not.
            # Persisted transcripts and LLM-generated compaction summaries are
            # untrusted inputs, so the user must name paths again in a fresh
            # terminal turn before tools can read them.
            path_policy.reset(mode=IntakeMode.DISCUSSION)
            _configure_intake_agent(
                agent,
                mode=path_policy.mode,
                starting_cwd=starting_cwd,
                path_policy=path_policy,
                file_tools=file_tools,
                launch_tool=launch_tool,
            )
            conv = prior
            _console.print(
                f"[green]Continuing your last conversation[/] "
                f"[dim]({escape(prior.title) or prior.conv_id})[/]\n"
            )
            _print_resumed_history(agent.messages)
        else:
            _console.print(
                "[yellow]No unfinished conversation to continue[/] "
                "[dim]— starting fresh.[/]\n"
            )

    def _persist() -> None:
        """Best-effort autosave; a failure must never break the chat."""
        try:
            save_conversation(conv, agent.messages, launched=state.launched)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            log.debug("intake conversation save failed: %s", exc)

    # Seed message: if the user gave one on the CLI, feed it as the first user turn
    pending_user_input: str | None = seed_message

    while True:
        if pending_user_input is None:
            try:
                user_text = await _read_user_line(session)
            except (EOFError, KeyboardInterrupt):
                _console.print("\n[yellow]aborted[/yellow]")
                return None
        else:
            user_text = pending_user_input
            pending_user_input = None
            _console.print(f"[dim]you (seed):[/dim] {user_text}\n")

        user_text = user_text.strip()
        if not user_text:
            continue

        # Slash commands stay client-side; agent never sees them
        if user_text.startswith("/"):
            action = _handle_slash(
                user_text,
                agent,
                list(agent.tools.values()),
                state,
                starting_cwd=starting_cwd,
                model=getattr(provider, "model", None),
                path_policy=path_policy,
            )
            if action == "quit":
                return None
            if action == "resume":
                # The user picked a past run via /resume — hand it straight back
                # so run_command resumes it instead of starting a fresh run.
                return state.resume_target
            if action == "conversation":
                # The user picked a saved conversation: _handle_slash already
                # reloaded it into the agent. Switch onward autosaves to that
                # record and keep chatting in this same intake loop.
                conv = state.resume_conversation
                _configure_intake_agent(
                    agent,
                    mode=path_policy.mode,
                    starting_cwd=starting_cwd,
                    path_policy=path_policy,
                    file_tools=file_tools,
                    launch_tool=launch_tool,
                )
                continue
            if action == "reset":
                _configure_intake_agent(
                    agent,
                    mode=path_policy.mode,
                    starting_cwd=starting_cwd,
                    path_policy=path_policy,
                    file_tools=file_tools,
                    launch_tool=launch_tool,
                )
                continue
            continue

        # Route every real user turn before the LLM sees it. Discussion mode
        # has no launch capability and no implicit cwd access; planning mode is
        # confined to the starting project plus explicitly named paths.
        mode = infer_intake_mode(user_text, path_policy.mode)
        path_policy.update(user_text, mode)
        # Approve the exact staged plan in controller code. The confirmation
        # never goes back through the model, so it cannot silently rewrite the
        # cwd/instruction or attach a different tool call after the user says go.
        if (
            mode == IntakeMode.PLANNING
            and state.pending_plan is not None
            and state.pending_plan_presented
            and is_explicit_launch_approval(user_text)
        ):
            agent.messages.append({"role": "user", "content": user_text})
            state.plan = state.pending_plan
            state.pending_plan = None
            state.pending_plan_presented = False
            _persist()
            return state.plan

        # Any non-approval real message edits or rejects the candidate. The
        # model must stage a fresh plan reflecting the new instruction.
        state.pending_plan = None
        state.pending_plan_presented = False
        _configure_intake_agent(
            agent,
            mode=mode,
            starting_cwd=starting_cwd,
            path_policy=path_policy,
            file_tools=file_tools,
            launch_tool=launch_tool,
        )

        # Hand off to the agent under a compact display.
        try:
            with IntakeDisplay(console=_console):
                reply = await agent.run(user_text)
        except KeyboardInterrupt:
            _console.print("\n[yellow]^C — interrupted[/yellow]")
            if typer.confirm("Quit intake?", default=False):
                return None
            continue

        if (
            mode == IntakeMode.PLANNING
            and state.pending_plan is not None
            and agent.stop_reason == "awaiting_user"
        ):
            state.pending_plan_presented = True
            _print_research_contract(state.pending_plan)
            _console.print(
                "[bold]Start this exact staged plan?[/bold] "
                "[dim](reply yes/go/start, or describe an edit)[/dim]\n"
            )
        if _is_llm_failure_reply(reply):
            _print_llm_failure_hint(
                provider=agent_config.provider,
                model=getattr(provider, "model", None),
                base_url=getattr(provider, "base_url", None),
            )
            continue

        # Autosave the turn (records launched=True once a plan is fired, so a
        # launched conversation is excluded from --continue).
        _persist()

        # The agent prints its own messages via core/agent.py's _print_*
        # helpers, and it only fires LaunchExperiment after the user has
        # explicitly approved the plan in conversation. So we hand the plan
        # straight back — the single Research Contract panel + go/no-go gate
        # lives in run.py, where the resolved hyperparameters (tree depth,
        # model, …) are known. The user can still preview with /contract.
        if state.launched:
            return state.plan


# ── helpers ────────────────────────────────────────────────────────


def _configure_intake_agent(
    agent: Agent,
    *,
    mode: IntakeMode,
    starting_cwd: Path,
    path_policy: IntakePathPolicy,
    file_tools: list[Tool],
    launch_tool: LaunchExperimentTool,
) -> None:
    """Atomically refresh prompt and capabilities for one user turn."""

    active_tools = [*file_tools, launch_tool] if mode == IntakeMode.PLANNING else file_tools
    agent.tools = {tool.name: tool for tool in active_tools}
    if mode == IntakeMode.DISCUSSION:
        agent.system_prompt = build_discussion_system_prompt(
            starting_cwd=str(starting_cwd),
            approved_scope=path_policy.describe(),
        )
    else:
        agent.system_prompt = build_system_prompt(
            starting_cwd=str(starting_cwd),
            approved_scope=path_policy.describe(),
        )


def _is_llm_failure_reply(reply: str | None) -> bool:
    if not reply:
        return False
    return reply.strip().lower().startswith("error: llm call failed")


def _provider_label(provider: LLMProvider) -> str:
    name = provider.__class__.__name__.lower()
    if "claude" in name:
        return "claude"
    if "openai" in name:
        return "openai"
    return name.replace("provider", "") or "unknown"


def _print_llm_failure_hint(*, provider: str, model: str | None, base_url: str | None) -> None:
    """Durable, user-facing error for intake model/endpoint failures."""
    lines = [
        "The planning model did not answer, so Arbor stayed in intake instead of launching a run.",
        "",
        f"provider: {provider or 'unknown'}",
        f"model: {model or 'unknown'}",
    ]
    if base_url:
        lines.append(f"endpoint: {base_url}")
    lines.extend([
        "",
        "Check that the endpoint is running and serving that model, or update `arbor config init ... --force`.",
        "Type /quit to leave, or fix the endpoint and send your request again.",
    ])
    _console.print()
    _console.print(Panel(
        "\n".join(lines),
        title="model unavailable",
        title_align="left",
        border_style="red",
    ))
    _console.print()


async def _read_user_line(session: PromptSession) -> str:
    """Prompt the user. Slash commands surface as a live dropdown.

    Must use prompt_async() rather than prompt() because run_intake runs
    inside an already-active asyncio event loop. Calling the sync prompt()
    from inside a running loop raises RuntimeError.
    """
    prompt = ANSI("\033[1;32myou\033[0m \033[2m›\033[0m ")
    return await session.prompt_async(prompt)


class _SlashCompleter(Completer):
    """Pop the slash-command menu the moment the user types '/'.

    Only triggers on a leading slash so plain prose isn't interrupted.
    """

    def __init__(
        self,
        commands: Iterable[tuple[str, str]],
        *,
        skills: Iterable[tuple[str, str]] = (),
        unloaded_skills: Callable[[], Iterable[str]] | None = None,
        plugins: Iterable[tuple[str, str, tuple[str, ...]]] = (),
        active_plugin: Callable[[], str | None] | None = None,
    ) -> None:
        self._commands = list(commands)
        self._skills = list(skills)
        self._unloaded_skills = unloaded_skills or (lambda: ())
        self._plugins = list(plugins)
        self._active_plugin = active_plugin or (lambda: None)
        # Compute a uniform display width so all rows line up.
        self._name_width = max(len(name) for name, _ in self._commands) if self._commands else 0
        self._skill_width = max(len(name) for name, _ in self._skills) if self._skills else 0
        self._plugin_width = max(len(name) for name, _, _ in self._plugins) if self._plugins else 0

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if not text.startswith("/"):
            return
        if text.startswith("/skill "):
            yield from self._skill_completions(text)
            return
        if text.startswith("/plugin "):
            yield from self._plugin_completions(text)
            return
        for name, desc in self._commands:
            if name.startswith(text):
                yield Completion(
                    name,
                    start_position=-len(text),
                    # Pad with two trailing spaces so the meta column doesn't
                    # touch the command name.
                    display=f"  {name:<{self._name_width}}  ",
                    display_meta=desc,
                )

    def _skill_completions(self, text: str):
        rest = text[len("/skill "):]
        parts = rest.split()
        action = parts[0].lower() if parts else ""
        actions = (("load", "reload an unloaded skill"), ("unload", "disable a loaded skill"))

        if action not in {"load", "unload"}:
            prefix = "" if text.endswith(" ") else action
            for name, desc in actions:
                if name.startswith(prefix):
                    yield Completion(
                        name,
                        start_position=-len(prefix) if prefix else 0,
                        display=f"  {name:<8}  ",
                        display_meta=desc,
                    )
            return

        prefix = "" if text.endswith(" ") else (parts[-1] if len(parts) > 1 else "")
        start_position = -len(prefix) if prefix else 0
        already_mentioned = set(parts[1:] if text.endswith(" ") else parts[1:-1])
        unloaded = set(self._unloaded_skills())
        for name, desc in self._skills:
            is_unloaded = name in unloaded
            if name in already_mentioned:
                continue
            if name.startswith(prefix):
                yield Completion(
                    name,
                    start_position=start_position,
                    display=f"{'×' if is_unloaded else '✓'} {name:<{self._skill_width}}  ",
                    display_meta=(f"unloaded skill · {desc}" if is_unloaded else f"loaded skill · {desc}"),
                )

    def _plugin_completions(self, text: str):
        rest = text[len("/plugin "):]
        parts = rest.split()
        action = parts[0].lower() if parts else ""
        actions = (("load", "load a plugin"), ("unload", "disable plugins for this run"), ("reset", "inherit config plugin"))

        if action not in {"load", "unload", "reset"}:
            prefix = "" if text.endswith(" ") else action
            for name, desc in actions:
                if name.startswith(prefix):
                    yield Completion(
                        name,
                        start_position=-len(prefix) if prefix else 0,
                        display=f"  {name:<8}  ",
                        display_meta=desc,
                    )
            return
        if action == "reset":
            return

        prefix = "" if text.endswith(" ") else (parts[-1] if len(parts) > 1 else "")
        start_position = -len(prefix) if prefix else 0
        active = self._active_plugin()
        plugin_name = parts[1] if len(parts) > 1 else ""
        if action == "load" and plugin_name and text.endswith(" "):
            profiles = next((profiles for name, _, profiles in self._plugins if name == plugin_name), ())
            for profile in profiles:
                yield Completion(profile, start_position=0, display=f"  {profile:<16}  ", display_meta="profile")
            return

        for name, desc, _profiles in self._plugins:
            is_loaded = name == active
            if name.startswith(prefix):
                yield Completion(
                    name,
                    start_position=start_position,
                    display=f"{'◆' if is_loaded else ' '} {name:<{self._plugin_width}}  ",
                    display_meta=(f"active plugin · {desc}" if is_loaded else desc),
                )


def _build_session(starting_cwd: Path | None = None, *, state: LaunchState | None = None) -> PromptSession:
    """One PromptSession per intake run (carries history + completer state)."""
    return PromptSession(
        history=InMemoryHistory(),
        completer=_SlashCompleter(
            SLASH_COMMANDS,
            skills=_available_skill_summaries(starting_cwd),
            unloaded_skills=(lambda: state.unloaded_skills) if state is not None else None,
            plugins=_available_plugin_summaries(starting_cwd),
            active_plugin=(lambda: state.plugin if state.plugin_mode == "load" else None) if state is not None else None,
        ),
        complete_while_typing=True,
        style=_MENU_STYLE,
    )


def _available_skill_summaries(starting_cwd: Path | None) -> list[tuple[str, str]]:
    try:
        from ...core.skill_registry import build_default_registry

        registry = build_default_registry(str(starting_cwd or Path.cwd()))
        return registry.summaries_with_source()
    except Exception:
        return []


def _available_skill_names(starting_cwd: Path | None) -> list[str]:
    return [name for name, _ in _available_skill_summaries(starting_cwd)]


def _loaded_skill_names(starting_cwd: Path | None, unloaded_skills: Iterable[str]) -> list[str]:
    unloaded = set(unloaded_skills)
    return [name for name in _available_skill_names(starting_cwd) if name not in unloaded]


def _available_plugin_summaries(starting_cwd: Path | None) -> list[tuple[str, str, tuple[str, ...]]]:
    try:
        from ...plugins import discover_plugins

        search_dirs = []
        if starting_cwd is not None and (starting_cwd / "plugins").is_dir():
            search_dirs.append(starting_cwd / "plugins")
        return [
            (p.name, f"{p.source} · {p.description}", p.profiles)
            for p in discover_plugins(search_dirs=search_dirs or None)
        ]
    except Exception:
        return []


# ── Slash-menu styling ─────────────────────────────────────────────
#
# Matches the welcome banner's cyan→magenta accent. Idle rows are dim
# grey with a cyan command name; selected row inverts to a saturated
# magenta background so it pops without being garish.
_MENU_STYLE = Style.from_dict({
    # Menu container & padding
    "completion-menu":                              "bg:#1c1c1c",
    "completion-menu.border":                       "fg:#5fafff bg:#1c1c1c",

    # Idle row
    "completion-menu.completion":                   "bg:#1c1c1c fg:#5fd7ff",
    "completion-menu.meta.completion":              "bg:#1c1c1c fg:#808080",

    # Selected row — magenta highlight
    "completion-menu.completion.current":           "bg:#af00d7 fg:#ffffff bold",
    "completion-menu.meta.completion.current":      "bg:#af00d7 fg:#ffd7ff",

    # Scrollbar (visible only when list overflows)
    "scrollbar.background":                         "bg:#1c1c1c",
    "scrollbar.button":                             "bg:#5fafff",
})


def _print_welcome(seed: str | None, *, starting_cwd: Path,
                   model: str | None = None,
                   base_url: str | None = None) -> None:
    """Render the splash. Wide brand line, dim context, single hint line."""
    import os

    from ..style import render_logo

    # ── brand block (ASCII art + tagline), shared with resume / --yes ──
    render_logo(_console)

    # ── context (two-col, dim) ───────────────────────────────────────
    home = os.path.expanduser("~")
    short_cwd = str(starting_cwd)
    if short_cwd == home:
        short_cwd = "~"
    elif short_cwd.startswith(home + os.sep):
        short_cwd = "~" + short_cwd[len(home):]

    rows: list[tuple[str, str]] = [("starting dir", short_cwd)]
    if model:
        endpoint_hint = f"  via {base_url}" if base_url else ""
        rows.append(("model", f"{model}{endpoint_hint}"))
    width = max(len(k) for k, _ in rows)
    for k, v in rows:
        _console.print(f"  [dim]{k.ljust(width)}[/dim]  [bold]{v}[/bold]")

    # ── divider + hint ───────────────────────────────────────────────
    _console.print()
    _console.rule(style="dim cyan")
    _console.print(
        "  [dim]Tell me what you want to research, or [/dim][bold]/resume[/bold]"
        "[dim] a past run. Type [/dim][bold]/help[/bold][dim] for commands, "
        "[/dim][bold]/quit[/bold][dim] to exit.[/dim]"
    )
    if seed is None:
        _console.print()


def _print_plan_accepted(plan: LaunchPlan | None) -> None:
    if plan is None:
        return
    lines = [f"[bold]Goal:[/bold] {plan.instruction}"]
    if plan.rationale:
        lines.append("")
        lines.append(plan.rationale)
    if plan.suggested_max_cycles is not None:
        lines.append("")
        lines.append(f"Suggested cycles: {plan.suggested_max_cycles}")
    if plan.suggested_max_turns is not None:
        lines.append(f"Suggested max turns: {plan.suggested_max_turns}")
    if plan.notes:
        lines.append("")
        lines.append("Notes:")
        for n in plan.notes:
            lines.append(f"  - {n}")
    _console.print()
    _console.print(Panel("\n".join(lines), title="Staged plan",
                         border_style="green"))


def _print_research_contract(plan: LaunchPlan) -> None:
    """Preview the pending plan (the /contract command).

    Mid-planning preview, so it shows only the plan-level fields the user has
    decided — target, objective, budget. The resolved hyperparameters (tree
    depth, model, review mode) are added to the full Research Contract panel at
    launch time in run.py, once config is resolved.
    """
    from ..i18n import detect_lang, t

    lang = detect_lang(plan.instruction, plan.rationale)
    table = Table.grid(padding=(0, 2))
    table.add_column(style="dim", justify="right")
    table.add_column(style="white", overflow="fold")
    table.add_row(t(lang, "target"), escape(plan.cwd))
    table.add_row(t(lang, "optimize"), escape(plan.instruction))
    budget = []
    sep = "" if lang == "zh" else " "
    if plan.suggested_max_cycles is not None:
        budget.append(f"{plan.suggested_max_cycles}{sep}{t(lang, 'branch_cycles')}")
    if plan.suggested_max_turns is not None:
        budget.append(f"{plan.suggested_max_turns}{sep}{t(lang, 'coordinator_turns')}")
    table.add_row(t(lang, "budget"),
                  escape(", ".join(budget) if budget else t(lang, "budget_defaults")))
    _console.print()
    _console.print(Panel(table, title=t(lang, "contract_title"), title_align="left", border_style="cyan"))
    _console.print()


def _visible_text(content: object) -> str:
    """Human-readable text of a message's content, ignoring tool plumbing."""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = [
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("type") in (None, "text") and isinstance(b.get("text"), str)
        ]
        return " ".join(p for p in parts if p).strip()
    return ""


def _print_resumed_history(messages: list[dict]) -> None:
    """Replay a reloaded conversation so the user can see the prior exchange.

    Renders user prompts and the agent's text replies; tool calls / results
    carry no prose and are skipped. No-op for an empty history.
    """
    rendered = False
    for m in messages:
        if m.get("_internal"):
            continue
        text = _visible_text(m.get("content"))
        if not text:
            continue
        if not rendered:
            _console.print("[dim]──────── previous conversation ────────[/dim]")
            rendered = True
        role = m.get("role")
        if role == "user":
            _console.print(f"[dim]you ›[/dim] {escape(text)}")
        elif role == "assistant":
            _console.print(f"[cyan]arbor ›[/cyan] {escape(text)}")
    if rendered:
        _console.print("[dim]───────────────────────────────────────[/dim]\n")


def _prompt_resume_any(
    convs: list[ConversationRecord],
    runs: list[ResumableSession],
    *,
    console: Console,
) -> ConversationRecord | ResumableSession | None:
    """Show conversations then runs as one numbered list; return the choice.

    Returns the picked :class:`ConversationRecord` or :class:`ResumableSession`,
    or ``None`` when the user starts fresh.
    """
    from ..resume_picker import _format_row, _humanize_age, _parse_iso

    items: list[ConversationRecord | ResumableSession] = [*convs, *runs]
    console.print()
    if convs:
        console.print("[bold cyan]Conversations[/] [dim](continue chatting)[/]")
        for i, c in enumerate(convs, 1):
            age = _humanize_age(_parse_iso(c.updated_at))
            title = escape(c.title or "(no messages yet)")
            console.print(
                f"  [bold]{i}[/]  [cyan]{escape(c.conv_id)}[/]  [dim]{age}[/]  "
                f"[dim]{c.turns} turn(s)[/]  [magenta]\\[chat][/]\n"
                f"      [dim]{title}[/]"
            )
    if runs:
        console.print("[bold cyan]Runs[/] [dim](replay the research engine)[/]")
        for i, s in enumerate(runs, len(convs) + 1):
            console.print(_format_row(i, s))
    console.print()

    while True:
        answer = typer.prompt(
            f"Start fresh, or resume one? [N / 1-{len(items)}]", default="N"
        ).strip().lower()
        if answer in ("", "n", "new"):
            return None
        if answer.isdigit() and 1 <= int(answer) <= len(items):
            return items[int(answer) - 1]
        console.print(f"[yellow]  enter N for a new chat, or 1-{len(items)} to resume[/]")


def _handle_slash(
    line: str,
    agent: Agent,
    tools: list[Tool],
    state: LaunchState,
    *,
    starting_cwd: Path | None = None,
    model: str | None = None,
    path_policy: IntakePathPolicy | None = None,
) -> str:
    cmd = line.lower().split()[0]
    if cmd == "/help":
        _console.print("[bold]commands[/bold] [dim](type / to bring up the menu)[/dim]")
        for name, desc in SLASH_COMMANDS:
            _console.print(f"  [cyan]{name:<8}[/cyan] [dim]{desc}[/dim]")
    elif cmd == "/resume":
        # List this project's past *conversations* and launched *runs* in one
        # picker. A conversation is reloaded into the live agent (keep chatting);
        # a run is handed back so run_command replays the orchestrator.
        from ..resume_picker import find_resumable_sessions

        convs = (
            [c for c in find_conversations(starting_cwd) if not c.launched and c.turns > 0]
            if starting_cwd else []
        )
        runs = (
            find_resumable_sessions(starting_cwd, include_subdirs=True)
            if starting_cwd else []
        )
        if not convs and not runs:
            where = escape(str(starting_cwd)) if starting_cwd else "this directory"
            _console.print(f"[yellow]Nothing to resume[/] under [dim]{where}[/].")
            _console.print(
                "  [dim]Conversations live in [/dim][cyan].arbor/conversations/[/cyan]"
                "[dim] and launched runs in [/dim][cyan].arbor/sessions/[/cyan][dim], "
                "per project. Start a new one by describing your goal.[/dim]"
            )
            return "continue"
        chosen = _prompt_resume_any(convs, runs, console=_console)
        if chosen is None:
            return "continue"        # user declined (N) → stay in intake
        if isinstance(chosen, ConversationRecord):
            agent.messages = seal_interrupted_tail(load_messages(chosen))
            if path_policy is not None:
                path_policy.reset(mode=IntakeMode.DISCUSSION)
            state.pending_plan = None
            state.pending_plan_presented = False
            state.resume_conversation = chosen
            _console.print(
                f"[green]Resumed conversation[/] "
                f"[dim]({escape(chosen.title) or chosen.conv_id})[/]\n"
            )
            _print_resumed_history(agent.messages)
            return "conversation"
        state.resume_target = chosen
        return "resume"
    elif cmd == "/status":
        _console.print("[bold]intake status[/bold]")
        if starting_cwd is not None:
            _console.print(f"  [dim]starting dir[/dim] {escape(str(starting_cwd))}")
        if model:
            _console.print(f"  [dim]model[/dim] {escape(model)}")
        _console.print(f"  [dim]plugin[/dim] {escape(_plugin_display(state))}")
        loaded_skills = ", ".join(_loaded_skill_names(starting_cwd, state.unloaded_skills)) or "—"
        unloaded_skills = ", ".join(state.unloaded_skills) if state.unloaded_skills else "—"
        _console.print(f"  [dim]skills loaded[/dim] {escape(loaded_skills)}")
        _console.print(f"  [dim]skills unloaded[/dim] {escape(unloaded_skills)}")
        _console.print(f"  [dim]turns[/dim] {agent.total_turns}")
        if path_policy is not None:
            _console.print(f"  [dim]mode[/dim] {escape(path_policy.mode.value)}")
            _console.print(
                f"  [dim]approved scope[/dim] {escape(path_policy.describe())}"
            )
        _console.print(
            f"  [dim]suppressed mixed tool calls[/dim] "
            f"{len(agent.suppressed_tool_uses)}"
        )
        pending = state.plan is not None or state.pending_plan is not None
        _console.print(f"  [dim]pending plan[/dim] {'yes' if pending else 'no'}")
    elif cmd == "/plugin":
        before = (state.plugin, state.plugin_profile, state.plugin_mode)
        _handle_plugin_command(line, state, starting_cwd=starting_cwd)
        after = (state.plugin, state.plugin_profile, state.plugin_mode)
        if after != before:
            state.pending_plan = None
            state.pending_plan_presented = False
    elif cmd == "/skill":
        before = tuple(state.unloaded_skills)
        _handle_skill_command(line, state, starting_cwd=starting_cwd)
        if tuple(state.unloaded_skills) != before:
            state.pending_plan = None
            state.pending_plan_presented = False
    elif cmd in ("/quit", "/abort"):
        return "quit"
    elif cmd == "/reset":
        agent.messages.clear()
        if path_policy is not None:
            path_policy.reset()
        state.plan = None
        state.pending_plan = None
        state.pending_plan_presented = False
        agent.suppressed_tool_uses.clear()
        _console.print("[dim]history cleared[/dim]")
        return "reset"
    elif cmd == "/tools":
        for t in tools:
            _console.print(f"  - {t.name}: {t.description.splitlines()[0]}")
    elif cmd in ("/plan", "/contract"):
        preview = state.plan or state.pending_plan
        if preview:
            if cmd == "/contract":
                _print_research_contract(preview)
            else:
                _print_plan_accepted(preview)
        else:
            _console.print("[dim]no contract yet[/dim]")
    else:
        _console.print(f"[yellow]unknown command: {cmd} (try /help)[/yellow]")
    return "continue"


def _plugin_display(state: LaunchState) -> str:
    if state.plugin_mode == "disabled":
        return "disabled (overrides config)"
    if state.plugin_mode != "load" or not state.plugin:
        return "config/default"
    if state.plugin_profile:
        return f"{state.plugin} ({state.plugin_profile})"
    return state.plugin


def _handle_plugin_command(
    line: str,
    state: LaunchState,
    *,
    starting_cwd: Path | None,
) -> None:
    parts = line.split()
    if len(parts) == 1:
        _console.print("[bold]plugin[/bold]")
        _console.print(f"  [dim]selection[/dim] {escape(_plugin_display(state))}")
        available = ", ".join(
            f"{name} ({meta.split(' · ', 1)[0]})"
            for name, meta, _profiles in _available_plugin_summaries(starting_cwd)
        ) or "none"
        _console.print(f"  [dim]available[/dim] {escape(available)}")
        _console.print("  [dim]usage[/dim] [cyan]/plugin load <name> [profile][/cyan]  [dim]or[/dim]  [cyan]/plugin unload[/cyan]")
        _console.print("  [dim]reset[/dim] [cyan]/plugin reset[/cyan] [dim](inherit config)[/dim]")
        return

    action = parts[1].lower()
    if action in {"reset", "clear", "default"}:
        previous = _plugin_display(state)
        state.plugin = None
        state.plugin_profile = None
        state.plugin_mode = "inherit"
        _console.print(f"[dim]plugin selection reset[/dim] {escape(previous)} → config/default")
        return

    if action in {"unload", "disable", "off", "none"}:
        previous = _plugin_display(state)
        state.plugin = None
        state.plugin_profile = None
        state.plugin_mode = "disabled"
        _console.print(f"[yellow]plugin disabled for this run[/yellow] [dim](was {escape(previous)})[/dim]")
        return

    if action == "load":
        if len(parts) < 3:
            _console.print("[yellow]usage: /plugin load <name> [profile][/yellow]")
            return
        name = parts[2].strip()
        profile = parts[3].strip() if len(parts) > 3 else None
    else:
        name = parts[1].strip()
        profile = parts[2].strip() if len(parts) > 2 else None

    summaries = {pname: (meta, profiles) for pname, meta, profiles in _available_plugin_summaries(starting_cwd)}
    if name not in summaries:
        available = ", ".join(summaries) or "none"
        _console.print(f"[yellow]unknown plugin: {escape(name)}[/yellow]")
        _console.print(f"[dim]available[/dim] {escape(available)}")
        return

    _meta, profiles = summaries[name]
    if profile and profile not in profiles:
        available = ", ".join(profiles) or "none"
        _console.print(f"[yellow]unknown profile for {escape(name)}: {escape(profile)}[/yellow]")
        _console.print(f"[dim]available profiles[/dim] {escape(available)}")
        return

    state.plugin = name
    state.plugin_profile = profile
    state.plugin_mode = "load"
    _console.print(f"[green]plugin active[/green] {escape(_plugin_display(state))}")


def _handle_skill_command(
    line: str,
    state: LaunchState,
    *,
    starting_cwd: Path | None,
) -> None:
    parts = line.split()
    if len(parts) == 1:
        _console.print("[bold]skills[/bold]")
        try:
            from ...core.skill_registry import build_default_registry

            registry = build_default_registry(str(starting_cwd or Path.cwd()))
            unloaded = set(state.unloaded_skills)
            loaded = ", ".join(name for name in registry.names() if name not in unloaded) or "—"
            unloaded_text = ", ".join(state.unloaded_skills) if state.unloaded_skills else "—"
            available = ", ".join(
                f"{name} ({meta.split(' · ', 1)[0]})"
                for name, meta in registry.summaries_with_source()
            ) or "none"
            _console.print(f"  [dim]loaded[/dim] {escape(loaded)}")
            _console.print(f"  [dim]unloaded[/dim] {escape(unloaded_text)}")
            _console.print(f"  [dim]available[/dim] {escape(available)}")
        except Exception:
            unloaded_text = ", ".join(state.unloaded_skills) if state.unloaded_skills else "—"
            _console.print(f"  [dim]unloaded[/dim] {escape(unloaded_text)}")
            pass
        _console.print("  [dim]usage[/dim] [cyan]/skill load <name...>[/cyan]  [dim]or[/dim]  [cyan]/skill unload <name...>[/cyan]")
        _console.print("  [dim]reset[/dim] [cyan]/skill reset[/cyan]")
        return

    action = parts[1].lower()
    if action in {"clear", "reset"}:
        state.unloaded_skills.clear()
        _console.print("[dim]all default skills loaded[/dim]")
        return

    names = [part.strip() for part in parts[2:] if part.strip()]
    if action not in {"load", "unload"} or not names:
        _console.print("[yellow]usage: /skill load <name...> or /skill unload <name...>[/yellow]")
        return

    known = set(_available_skill_names(starting_cwd))
    unknown = [name for name in names if name not in known]
    if unknown:
        _console.print(f"[yellow]unknown skill(s): {escape(', '.join(unknown))}[/yellow]")
        return

    unloaded = set(state.unloaded_skills)
    if action == "unload":
        changed = [name for name in names if name not in unloaded]
        unloaded.update(names)
        state.unloaded_skills = sorted(unloaded)
        if changed:
            _console.print(f"[yellow]skills unloaded[/yellow] {escape(', '.join(changed))}")
        else:
            _console.print(f"[dim]already unloaded[/dim] {escape(', '.join(names))}")
    else:
        changed = [name for name in names if name in unloaded]
        unloaded.difference_update(names)
        state.unloaded_skills = sorted(unloaded)
        if changed:
            _console.print(f"[green]skills loaded[/green] {escape(', '.join(changed))}")
        else:
            _console.print(f"[dim]already loaded[/dim] {escape(', '.join(names))}")
