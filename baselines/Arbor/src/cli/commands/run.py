"""`arbor run` — chat with the intake agent, then run the coordinator."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import typer

from ..._app import CONFIG_DIR_NAME
from .._constants import (
    DEFAULT_WEBUI_PORT,
    INTAKE_LLM_PROVIDER_RETRIES,
    INTAKE_LLM_RETRY_ATTEMPTS,
    INTAKE_LLM_RETRY_BASE_DELAY,
    INTAKE_LLM_RETRY_MAX_DELAY,
    INTAKE_LLM_TIMEOUT,
    INTAKE_REASONING_EFFORT,
    WEBUI_PORT_SCAN,
)


_AUTO_CONFIG_NAMES = ("research_config.yaml", "arbor.yaml", "autoresearch.yaml")


def run_command(
    instruction: str | None = typer.Argument(
        None,
        help="Research goal seed, e.g. 'maximize dev score without changing eval/data'. Omit to start with intake chat.",
    ),
    cwd: Path = typer.Option(Path("."), "--cwd",
                             help="Project directory hint. Intake verifies/adjusts it unless --yes is used."),
    config: Path | None = typer.Option(
        None, "--config", "-c",
        help="Project YAML config. Defaults to research_config.yaml / arbor.yaml / autoresearch.yaml in the target project.",
    ),
    max_cycles: int | None = typer.Option(None, "--max-cycles",
                                          help="Max completed/skipped/failed idea experiments before finalizing."),
    max_turns: int | None = typer.Option(None, "--max-turns",
                                         help="Hard cap on coordinator ReAct turns. Use as a cost/runaway safety valve."),
    intake_max_turns: int = typer.Option(30, "--intake-max-turns",
                                         help="Max internal agent turns per intake message. Rarely needed."),
    run_name: str | None = typer.Option(None, "--run-name", help="Session name under .arbor/sessions/. Defaults to timestamp."),
    resume: bool = typer.Option(
        False, "--resume",
        help="Resume an interrupted run from its checkpoint (idea tree + message history) "
             "in the existing workspace/session, instead of starting fresh.",
    ),
    continue_latest: bool = typer.Option(
        False, "--continue", "-C",
        help="Continue the most recent unfinished intake conversation in this directory "
             "(like `claude -c`). Reloads the planning chat so you pick up where you left off. "
             "(-c is --config; use -C or --continue.)",
    ),
    workspace_dir: Path | None = typer.Option(
        None, "--workspace-dir",
        help=f"Session/artifact directory override. Default: <target>/{CONFIG_DIR_NAME}/sessions/<run_name>.",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show lower-level coordinator logs."),
    yes_cwd: Path | None = typer.Option(
        None, "--yes-cwd",
        help="Target project directory when --yes skips intake. Required with --yes.",
    ),
    yes: bool = typer.Option(False, "--yes", "-y",
                             help="Skip intake chat and launch directly from instruction + --yes-cwd."),
    no_dashboard_input: bool = typer.Option(
        False, "--no-dashboard-input",
        help="Disable live terminal input. Prompts/review gates auto-continue after timeout.",
    ),
    followup: bool = typer.Option(
        True, "--followup/--no-followup",
        help="After REPORT.md, open a read-only Q&A prompt about the finished run.",
    ),
    verbose_preflight: bool = typer.Option(
        False, "--verbose-preflight",
        help="Print successful preflight checks too. Default shows only failures/warnings.",
    ),
    webui_port: int | None = typer.Option(
        None, "--webui-port",
        help=f"Read-only browser monitor port. Default auto-starts near {DEFAULT_WEBUI_PORT} for interactive runs.",
    ),
    no_webui: bool = typer.Option(
        False, "--no-webui",
        help="Do not start the read-only browser monitor.",
    ),
    interaction_mode: str | None = typer.Option(
        None, "--interaction-mode", "--mode",
        help="Human-in-loop mode: auto, direction (ask where to explore), review (approve/edit ideas), collaborative (both).",
    ),
    allow_non_base_branch: bool = typer.Option(
        False, "--allow-non-base-branch",
        help="Allow starting from the current non-main branch. Useful for dev, risky for benchmarks.",
    ),
) -> None:
    """Start an AI-powered research session.

    Default flow:
      1. open an interactive chat with the intake agent
      2. the agent confirms with you which project directory to work on
         (the --cwd flag is just a hint — the agent can change it)
      3. when you and the agent agree on a plan, the agent calls LaunchExperiment
      4. you confirm the Research Contract shown in the terminal
      5. quick safety check against the chosen project dir
      6. coordinator runs to completion, REPORT.md is written

    Pass `--yes "instruction" --yes-cwd /path/to/project` to skip the chat.
    """
    from ...coordinator.config import CoordinatorConfig
    from ...coordinator.main import create_provider
    from ...coordinator.orchestrator import CoordinatorOrchestrator
    from ...events import EventBus
    from ...events.subscribers.file_logger import JsonlFileLogger
    from ...events.subscribers.stats_collector import StatsCollector
    from ...report import generate_report
    from ..preflight import PreflightChecker
    from ..intake import run_intake, LaunchPlan
    from ..i18n import detect_lang, t as i18n_t
    from ..post_run import render_final_report, run_post_run_repl
    from ..run_dashboard import RunDashboard
    from ..run_state import RunState
    from ..companion import Companion
    from ...webui import start_webui
    from ..user_config import llm_defaults, cli_defaults, load_user_defaults

    starting_cwd = cwd.resolve()
    if not starting_cwd.exists():
        typer.secho(f"error: starting cwd does not exist: {starting_cwd}",
                    fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)

    starting_config_path = _resolve_config_path(config, starting_cwd)
    starting_project_defaults = _load_project_defaults(starting_config_path)

    # ── Resolve LLM config: CLI flag > project config > user config > built-in defaults ──
    user_llm = llm_defaults()
    user_cli = cli_defaults()

    # First run: no global config at all. In an interactive terminal, walk the
    # user through `arbor setup` once instead of failing on missing credentials,
    # then re-read the freshly written defaults. Skipped for headless/--yes runs.
    if not load_user_defaults() and not yes and sys.stdin and sys.stdin.isatty():
        from .setup_cmd import run_setup_wizard
        if run_setup_wizard():
            user_llm = llm_defaults()
            user_cli = cli_defaults()

    eff = _resolve_effective_options(
        project_defaults=starting_project_defaults,
        user_llm=user_llm,
        user_cli=user_cli,
        max_cycles=max_cycles,
        max_turns=max_turns,
    )
    _validate_effective_options(eff)

    if yes:
        if not instruction:
            typer.secho("error: --yes requires an instruction argument",
                        fg=typer.colors.RED, err=True)
            raise typer.Exit(code=2)
        if not yes_cwd:
            typer.secho("error: --yes requires --yes-cwd <project-dir>",
                        fg=typer.colors.RED, err=True)
            raise typer.Exit(code=2)

    # Fail before spending intake tokens when credentials are definitely
    # missing. Full project preflight still runs later against the final cwd.
    from ..style import render_error_panel, console as _console, render_status, render_logo, render_panel
    llm_probe = PreflightChecker(
        cwd=starting_cwd,
        provider=eff["provider"],
        explicit_api_key=eff["api_key"],
        verbose=verbose_preflight,
    ).check_llm_credentials(render=verbose_preflight)
    if llm_probe.status == "fail":
        body = llm_probe.message
        if llm_probe.hint:
            body += f"\nhint: {llm_probe.hint}"
        render_error_panel("cannot start intake — LLM credentials missing", body)
        raise typer.Exit(code=2)

    # Build a probe CoordinatorConfig just to construct a provider for the
    # intake chat. The real cwd/task come later from the plan.
    def _probe_config() -> CoordinatorConfig:
        kw: dict = {
            "cwd": str(starting_cwd),
            "task": instruction or "",
            "verbose": verbose,
            # Intake is interactive: endpoint/model mistakes should become a
            # visible prompt quickly, not a several-minute blank spinner.
            "llm_timeout": INTAKE_LLM_TIMEOUT,
            "llm_provider_retries": INTAKE_LLM_PROVIDER_RETRIES,
            "llm_retry_attempts": INTAKE_LLM_RETRY_ATTEMPTS,
            "llm_retry_base_delay": INTAKE_LLM_RETRY_BASE_DELAY,
            "llm_retry_max_delay": INTAKE_LLM_RETRY_MAX_DELAY,
            # Planning conversation: lighter reasoning keeps each turn snappy.
            "reasoning_effort": INTAKE_REASONING_EFFORT,
        }
        if eff["provider"] is not None:
            kw["provider"] = eff["provider"]
        if eff["model"] is not None:
            kw["model"] = eff["model"]
        if eff["base_url"] is not None:
            kw["base_url"] = eff["base_url"]
        if eff["api_key"] is not None:
            kw["api_key"] = eff["api_key"]
        if eff["openai_api"] is not None:
            kw["openai_api"] = eff["openai_api"]
        return CoordinatorConfig(**kw)

    # ── 1. Resume picker / intake chat (or skip with --yes) ─────────
    plan_cwd: Path
    refined_instruction: str
    suggested_max_cycles: int | None = None
    suggested_max_turns: int | None = None
    selected_plugin: str | None = None
    selected_plugin_profile: str | None = None
    selected_plugin_mode: str = "inherit"
    unloaded_skills: list[str] = []

    # Decide what to launch:
    #   --yes        → headless run from the CLI instruction
    #   otherwise    → the intake main interface, which returns either a fresh
    #                  LaunchPlan or, if the user ran /resume, a ResumableSession.
    # (There's no longer an auto resume-picker at startup — resume is an explicit
    # /resume command inside the main interface.)
    from ..resume_picker import ResumableSession

    resume_session = None
    if yes:
        plan_cwd = Path(yes_cwd).expanduser().resolve()
        refined_instruction = instruction or ""
        if sys.stdout.isatty():     # headless --yes still shows the logo in a TTY
            render_logo()
    else:
        try:
            intake_provider = create_provider(_probe_config())
        except ValueError as exc:
            typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=2) from exc
        try:
            outcome: LaunchPlan | ResumableSession | None = asyncio.run(run_intake(
                provider=intake_provider,
                starting_cwd=starting_cwd,
                seed_message=instruction,
                intake_max_turns=intake_max_turns,
                continue_latest=continue_latest,
            ))
        except KeyboardInterrupt:
            typer.secho("\n^C — aborted before run started", fg=typer.colors.YELLOW, err=True)
            raise typer.Exit(code=130)
        if outcome is None:
            typer.secho("\nAborted by user.", fg=typer.colors.YELLOW)
            raise typer.Exit(code=0)
        if isinstance(outcome, ResumableSession):
            resume_session = outcome     # user ran /resume → fall through to resume setup
        else:
            plan_cwd = Path(outcome.cwd).resolve()
            unloaded_skills = list(outcome.unloaded_skills)
            refined_instruction = outcome.instruction
            if outcome.notes:
                refined_instruction += (
                    "\n\nAdditional user constraints:\n- "
                    + "\n- ".join(outcome.notes)
                )
            selected_plugin = outcome.plugin
            selected_plugin_profile = outcome.plugin_profile
            selected_plugin_mode = outcome.plugin_mode
            suggested_max_cycles = outcome.suggested_max_cycles
            suggested_max_turns = outcome.suggested_max_turns

    if resume_session is not None:
        # Equivalent to `--run-name <x> --resume`: reuse the session dir and let
        # the orchestrator replay history. The logo was already shown by intake.
        resume = True
        run_name = resume_session.run_name
        workspace_dir = resume_session.session_dir
        plan_cwd = resume_session.cwd
        refined_instruction = resume_session.task

    if not plan_cwd.exists() or not plan_cwd.is_dir():
        typer.secho(f"error: target project dir not found: {plan_cwd}",
                    fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)

    final_config_path = starting_config_path if config is not None else _resolve_config_path(None, plan_cwd)
    final_project_defaults = _load_project_defaults(
        final_config_path,
        plugin_mode=selected_plugin_mode,
        plugin=selected_plugin,
        plugin_profile=selected_plugin_profile,
        plugin_search_dir=plan_cwd / "plugins",
    )
    eff = _resolve_effective_options(
        project_defaults=final_project_defaults,
        user_llm=user_llm,
        user_cli=user_cli,
        max_cycles=max_cycles,
        max_turns=max_turns,
    )
    _validate_effective_options(eff)

    # ── 2. Quick essentials check against plan_cwd ─────────────────
    #
    # Intake is deliberately read-only. Run the deterministic essentials
    # preflight after the staged plan is approved, and surface only failures by
    # default so planning stays conversational without hiding execution.
    #
    # Resolve the plugin's eval_contract so the contamination preflight can
    # warn (zero-network) when the benchmark is likely in pretraining data.
    # Covers both CLI-selected ("load") and project-YAML ("inherit") plugins;
    # any failure degrades to no contract — same behaviour as before.
    preflight_eval_contract: dict | None = None
    if selected_plugin_mode != "disabled":
        _plugin_name = selected_plugin or final_project_defaults.get("plugin")
        if _plugin_name:
            try:
                from ...plugins.base import load_plugin
                preflight_eval_contract = load_plugin(
                    str(_plugin_name), [plan_cwd / "plugins"]
                ).eval_contract
            except Exception:  # noqa: BLE001 - preflight must never block a run
                preflight_eval_contract = None
    checker = PreflightChecker(
        cwd=plan_cwd,
        provider=eff["provider"],
        explicit_api_key=eff["api_key"],
        verbose=verbose_preflight,
        eval_contract=preflight_eval_contract,
    )
    results = checker.run_all_collect(render=verbose_preflight)
    fatal = [r for r in results if r.status == "fail"]
    if fatal:
        body_lines = []
        for r in fatal:
            body_lines.append(f"[{r.name}] {r.message}")
            if r.hint:
                body_lines.append(f"  hint: {r.hint}")
        render_error_panel("preflight failed — fix and re-run",
                           "\n".join(body_lines))
        raise typer.Exit(code=2)

    # ── 3. Build session and launch the coordinator ─────────────────
    resolved_run_name = run_name or f"run_{datetime.now():%Y%m%d_%H%M%S}"
    session_dir = (workspace_dir or (plan_cwd / CONFIG_DIR_NAME / "sessions" / resolved_run_name)).resolve()
    session_dir.mkdir(parents=True, exist_ok=True)

    # Build the run config through the shared resolver: resolve_config re-reads
    # the YAML (applying plugin/profile/structured blocks). LLM defaults come
    # from arbor setup / project YAML; run-time CLI only overrides execution
    # controls such as budget and interaction mode.
    from ...core.config_resolve import resolve_config

    overrides: dict = {
        "cwd": str(plan_cwd),
        "task": refined_instruction,
        "verbose": verbose,
        "workspace_dir": str(session_dir),
    }
    _add_effective_llm_overrides(overrides, eff)
    for key in ("max_cycles", "max_turns"):
        if eff.get(key) is not None:
            overrides[key] = eff[key]
    if eff.get("max_cycles") is None and suggested_max_cycles is not None:
        overrides["max_cycles"] = suggested_max_cycles
    if eff.get("max_turns") is None and suggested_max_turns is not None:
        overrides["max_turns"] = suggested_max_turns
    if selected_plugin_mode == "disabled":
        overrides["_plugin_disabled"] = True
    elif selected_plugin_mode == "load" and selected_plugin:
        overrides["plugin"] = selected_plugin
        overrides["_plugin_search_dirs"] = [str(plan_cwd / "plugins")]
        if selected_plugin_profile:
            overrides["plugin_profile"] = selected_plugin_profile
        else:
            overrides["_plugin_profile_cleared"] = True
    if unloaded_skills:
        overrides["disabled_skills"] = unloaded_skills

    config = resolve_config(yaml_path=final_config_path, cli_overrides=overrides, role="coordinator")
    config.resume = resume
    if resume:
        # Resuming continues an existing run's branches; being on the working
        # trunk (where the last run left you) is expected, so don't enforce the
        # base-branch guard — it would otherwise refuse to resume.
        config.require_base_branch = False
    else:
        # Fresh run: the trunk must be cut from the base branch. If a previous
        # run left this repo on a non-base branch, offer to recover instead of
        # crashing deep inside the run.
        from ..branch_guard import resolve_start_branch
        _interactive = bool(not yes and sys.stdin and sys.stdin.isatty())
        if resolve_start_branch(plan_cwd, config,
                                allow_non_base=allow_non_base_branch,
                                interactive=_interactive,
                                console=_console) == "abort":
            raise typer.Exit(code=1)
    if webui_port is not None:
        config.ui.webui_port = webui_port
    requested_interaction = _resolve_requested_interaction_mode(
        interaction_mode=interaction_mode,
    )
    if requested_interaction is not None:
        config.ui.interaction_mode = requested_interaction

    # The single Research Contract confirmation. Planning happened in the intake
    # conversation; this is the one panel the user signs off on, showing the
    # resolved run settings — target, objective, budget, and the hyperparameters
    # (tree depth, model, …) that only exist after config resolution. Labels are
    # localized to the user's language; --yes prints it without confirming.
    _lang = detect_lang(refined_instruction)
    render_panel(i18n_t(_lang, "contract_title"),
                 _launch_param_rows(config, session_dir, _lang,
                                    str(plan_cwd), refined_instruction or "",
                                    unloaded_skills=unloaded_skills),
                 border_style="cyan")
    if not yes and sys.stdin and sys.stdin.isatty():
        try:
            if not typer.confirm(i18n_t(_lang, "confirm_launch"), default=True):
                typer.secho("aborted.", fg=typer.colors.YELLOW)
                raise typer.Exit(code=0)
        except typer.Abort:
            raise typer.Exit(code=130)

    # Default to the prompt_toolkit Application: it owns the whole screen, so
    # the Rich dashboard renders above a real input line, CJK input methods
    # compose inline, and nothing flickers. ARBOR_DASHBOARD_INPUT_MODE=raw|line
    # selects the legacy Rich-Live fallbacks (also used automatically with no
    # TTY). Only warn when the user explicitly asked for a legacy mode.
    dashboard_input_mode = _resolve_dashboard_input_mode(os.environ)
    requested_dashboard_input_mode = os.environ.get("ARBOR_DASHBOARD_INPUT_MODE", "").strip().lower()
    if not no_dashboard_input and requested_dashboard_input_mode in ("raw", "line"):
        if dashboard_input_mode == "line" and requested_dashboard_input_mode == "raw":
            render_status(
                "raw dashboard input is unsafe in this terminal (CJK input-method crash); "
                "using Enter-to-send line mode",
                style="yellow", glyph="!")
        else:
            render_status(
                f"using legacy '{dashboard_input_mode}' dashboard input "
                "(default is the full prompt_toolkit input)",
                style="cyan", glyph="›")

    if config.ui.interaction_mode in ("review", "collaborative"):
        if no_dashboard_input:
            render_status(
                "review interaction needs the dashboard input box to answer gates; with "
                f"--no-dashboard-input each idea auto-approves after {config.ui.review_timeout}s",
                style="yellow", glyph="!")
    if config.ui.interaction_mode != "auto" and no_dashboard_input:
        render_status(
            "interaction mode needs the dashboard input box for live replies; with "
            "--no-dashboard-input idea-stage prompts continue only after their timeout",
            style="yellow", glyph="!")

    bus = EventBus()
    stats = StatsCollector()
    stats.attach(bus)
    events_path = session_dir / "events.jsonl"

    # The previous "arbor run" banner panel (task/cwd/model/cycles/...)
    # is now subsumed by the live dashboard's own header, so we go
    # straight from intake → dashboard with no intermediate ceremony.

    provider_obj = create_provider(config)

    exit_reason = "ok"
    report: str | None = None
    exit_code = 0
    run_state = RunState(
        run_name=resolved_run_name,
        task=refined_instruction or "",
        cwd=str(plan_cwd),
        model=config.effective_meta_model,
        total_cycles=config.max_cycles,
        session_dir=str(session_dir),
        conversation_path=str(session_dir / "conversation.md"),
    )
    # On resume the engine loads the idea tree but does NOT replay the historical
    # idea.* events, so the dashboard's counters/tree would start empty (e.g.
    # "branches 0/20" even for a finished run). Seed the ledger straight from the
    # persisted tree — directly, not via the bus, so events.jsonl (opened in
    # append mode on resume) is never double-written. Purely cosmetic, so a
    # failure here must never break the resume.
    if resume:
        try:
            _tree_path = config.tree_json_path
            if _tree_path.exists():
                run_state.seed_from_tree(json.loads(_tree_path.read_text(encoding="utf-8")))
        except Exception:
            pass
    # Read-only Q&A companion (#11): answers typed questions mid-run from a
    # separate agent grounded in the checkpoint's messages.jsonl, without
    # touching the research run. Skip it when there's no input box to type in.
    companion = None
    if not no_dashboard_input:
        companion = Companion(
            provider=create_provider(config),
            model=config.effective_meta_model,
            agent_cwd=str(plan_cwd),
            workspace_dir=str(session_dir),
            run_state=run_state,
            messages_path=config.coordinator_dir / "messages.jsonl",
            tree_path=config.tree_json_path,
            events_path=events_path,
            gate_submit=lambda gate, value: bus.emit(
                "user.input_received",
                {"node_id": gate.get("node_id", ""), "value": value},
            ),
        )
    # Read-only WebUI (#7): independent browser monitor on its own port,
    # consuming the same event bus. On by default for interactive runs:
    #   --no-webui              → off
    #   --webui-port N / config → that exact port (single try)
    #   otherwise (a TTY)       → DEFAULT_WEBUI_PORT, auto-rolling if it's taken
    # Headless/non-TTY runs stay off unless a port was explicitly chosen, so a
    # piped/CI invocation never binds a surprise port.
    webui = None
    if not no_webui:
        explicit_port = config.ui.webui_port  # set by --webui-port or config
        if explicit_port:
            preferred, auto = explicit_port, False
        elif sys.stdin and sys.stdin.isatty():
            preferred, auto = DEFAULT_WEBUI_PORT, True
        else:
            preferred, auto = None, False
        if preferred:
            webui = start_webui(run_state, bus, preferred=preferred,
                                auto=auto, scan=WEBUI_PORT_SCAN,
                                companion=companion,
                                enable_input=not no_dashboard_input)
            if webui is not None:
                run_state.set_webui_url(webui.browser_url)
                if webui.interactive:
                    render_status(f"WebUI monitor ready (interactive) — {webui.browser_url}",
                                  style="cyan", glyph="›")
                else:
                    render_status(f"WebUI monitor ready (read-only) — {webui.url}",
                                  style="cyan", glyph="›")

    with JsonlFileLogger(events_path) as flog, RunDashboard(
        run_state,
        bus,
        enable_input=not no_dashboard_input,
        companion=companion,
        input_mode=dashboard_input_mode,
    ):
        flog.attach(bus)
        orchestrator = CoordinatorOrchestrator(config=config, provider=provider_obj, bus=bus)
        try:
            report = asyncio.run(orchestrator.run())
        except KeyboardInterrupt:
            exit_reason = "interrupted"
            exit_code = 130
            render_status("interrupted by user (^C)", style="yellow", glyph="!")
        except Exception as exc:
            exit_reason = "error"
            exit_code = 1
            render_error_panel("run failed", repr(exc))

    if companion is not None:
        companion.close()
    if webui is not None:
        webui.stop()

    raw_report_path: Path | None = None
    if report:
        raw_report_path = session_dir / "COORDINATOR_FINAL_REPORT.txt"
        try:
            raw_report_path.write_text(report + "\n", encoding="utf-8")
        except OSError as exc:
            render_status(f"failed to write coordinator final report ({exc!r})", style="yellow", glyph="!")
            raw_report_path = None

    # Always try to write a report — partial results are still useful.
    report_path: Path | None = None
    try:
        report_path = generate_report(
            session_dir,
            instruction=refined_instruction,
            event_stats=stats.stats,
            exit_reason=exit_reason,
        )
        render_status(f"report written: {report_path}", style="green", glyph="✓")
    except Exception as exc:
        render_status(f"failed to write REPORT.md ({exc!r})", style="yellow", glyph="!")

    rendered_report = False
    if report_path is not None and report_path.exists():
        try:
            render_final_report(report_path.read_text(encoding="utf-8"), report_path=report_path)
            rendered_report = True
        except OSError as exc:
            render_status(f"failed to read REPORT.md ({exc!r})", style="yellow", glyph="!")
    if not rendered_report and report:
        render_final_report(report, report_path=raw_report_path)

    if followup:
        try:
            followup_report_path = report_path or raw_report_path
            followup_provider = create_provider(config)
            asyncio.run(run_post_run_repl(
                provider=followup_provider,
                project_cwd=plan_cwd,
                session_dir=session_dir,
                report_path=followup_report_path,
                instruction=refined_instruction,
                model=config.effective_meta_model,
                enabled=True,
            ))
        except KeyboardInterrupt:
            _console.print("\n[yellow]^C — leaving follow-up[/yellow]")

    if exit_code:
        raise typer.Exit(code=exit_code)


def _resolve_config_path(explicit: Path | None, cwd: Path) -> Path | None:
    """Resolve an explicit or auto-discovered project config path."""
    if explicit is not None:
        candidate = explicit.expanduser()
        if not candidate.is_absolute():
            cwd_candidate = (cwd / candidate).resolve()
            shell_candidate = candidate.resolve()
            candidate = cwd_candidate if cwd_candidate.exists() else shell_candidate
        if not candidate.exists() or not candidate.is_file():
            typer.secho(f"error: config file not found: {candidate}",
                        fg=typer.colors.RED, err=True)
            raise typer.Exit(code=2)
        return candidate

    for name in _AUTO_CONFIG_NAMES:
        candidate = cwd / name
        if candidate.exists() and candidate.is_file():
            return candidate.resolve()
    return None


def _load_project_defaults(
    config_path: Path | None,
    *,
    plugin_mode: str = "inherit",
    plugin: str | None = None,
    plugin_profile: str | None = None,
    plugin_search_dir: Path | None = None,
) -> dict[str, Any]:
    """Flatten a project YAML (incl. plugin/profile overrides) for effective-
    option resolution.

    Only used to compute the interactive ``eff`` display/precedence; the final
    config is built by :func:`resolve_config`, which re-reads the YAML and
    additionally coerces structured blocks and validates.
    """
    if config_path is None:
        return {}
    from ...core.config_resolve import load_layered_defaults

    try:
        directives: dict[str, Any] = {}
        search_dirs: list[Path] = []
        if plugin_mode == "disabled":
            directives["_plugin_disabled"] = True
        elif plugin_mode == "load" and plugin:
            directives["plugin"] = plugin
            if plugin_profile:
                directives["plugin_profile"] = plugin_profile
            else:
                directives["_plugin_profile_cleared"] = True
            if plugin_search_dir is not None:
                search_dirs.append(plugin_search_dir)
        return load_layered_defaults(
            config_path,
            "coordinator",
            cli_directives=directives or None,
            extra_search_dirs=search_dirs or None,
        )
    except Exception as exc:
        typer.secho(f"error: failed to load config {config_path}: {exc}",
                    fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc


def _launch_param_rows(config: Any, session_dir: Path, lang: str,
                       target: str, instruction: str, *,
                       unloaded_skills: list[str] | None = None) -> list[tuple[str, str]]:
    """Build the (label, value) rows for the localized Research Contract panel.

    Kept deliberately minimal — target, objective, budget, max depth — plus the
    resolved model/provider/review/session rows. ``max_tree_depth`` only exists
    after config resolution, which is why this panel lives here rather than in
    intake. Labels are localized; values stay as-is.
    """
    from ..i18n import t

    sep = "" if lang == "zh" else " "
    budget: list[str] = []
    if config.max_cycles is not None:
        budget.append(f"{config.max_cycles}{sep}{t(lang, 'branch_cycles')}")
    if config.max_turns is not None:
        budget.append(f"{config.max_turns}{sep}{t(lang, 'coordinator_turns')}")

    depth = config.max_tree_depth
    rows: list[tuple[str, str]] = [
        (t(lang, "target"), target),
        (t(lang, "optimize"), instruction),
        (t(lang, "budget"),
         ", ".join(budget) if budget else t(lang, "budget_defaults")),
        (t(lang, "tree_depth"),
         str(depth) if depth is not None else t(lang, "unlimited")),
        (t(lang, "model"), str(config.effective_meta_model)),
    ]
    if getattr(config, "provider", None):
        rows.append((t(lang, "provider"), str(config.provider)))
    if getattr(config, "base_url", None):
        rows.append((t(lang, "endpoint"), str(config.base_url)))
    plugin = getattr(config, "plugin", None)
    if plugin is not None and getattr(plugin, "name", None):
        rows.append((t(lang, "plugin"), str(plugin.name)))
    if unloaded_skills:
        rows.append((t(lang, "skills"), "unloaded: " + ", ".join(unloaded_skills)))
    rows.append((t(lang, "interaction_mode"), str(config.ui.interaction_mode)))
    if getattr(config.ui, "webui_port", None):
        rows.append((t(lang, "webui"), str(config.ui.webui_port)))
    rows.append((t(lang, "session_dir"), str(session_dir)))
    return rows


def _resolve_requested_interaction_mode(
    *,
    interaction_mode: str | None,
) -> str | None:
    """Resolve the unified human-interaction CLI control."""
    if interaction_mode is None:
        return None
    mode = interaction_mode.lower()
    if mode not in ("auto", "direction", "review", "collaborative"):
        typer.secho(
            "error: --interaction-mode must be one of auto, direction, review, collaborative",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)
    return mode


def _live_input_unsafe_terminal(env: "os._Environ[str] | dict[str, str]") -> bool:
    """True if the dashboard's raw cbreak input box would risk a crash here.

    macOS Terminal.app crashes when stdin is in cbreak mode and a CJK input
    method composes a character. We detect it via ``TERM_PROGRAM`` (Terminal.app
    sets ``Apple_Terminal``; iTerm2 sets ``iTerm.app``; VSCode sets ``vscode``).
    ``ARBOR_FORCE_DASHBOARD_INPUT`` (1/true/yes) forces raw input back on for
    users who know their setup is fine.
    """
    if env.get("ARBOR_FORCE_DASHBOARD_INPUT", "").strip().lower() in ("1", "true", "yes"):
        return False
    return env.get("TERM_PROGRAM") == "Apple_Terminal"


def _resolve_dashboard_input_mode(env: "os._Environ[str] | dict[str, str]") -> str:
    """Choose the dashboard input mode.

    ``app`` is the default: a prompt_toolkit Application owns the whole screen
    (the Rich dashboard is rendered as a content region above a real pt input
    line). Because prompt_toolkit owns the cursor, CJK input methods compose
    inline at the caret and there is no flicker — it works in every terminal,
    including macOS Terminal.app, so there is no special-case downgrade.

    The legacy Rich-``Live`` paths remain as explicit fallbacks for terminals
    where the pt Application can't own the TTY or when the user opts in:
    ``raw`` (in-panel cbreak caret; downgrades to ``line`` where cbreak is
    unsafe) and ``line`` (Enter-to-send cooked input). ``prompt`` is accepted as
    a legacy alias for ``app``.
    """
    requested = env.get("ARBOR_DASHBOARD_INPUT_MODE", "").strip().lower()
    if requested == "prompt":
        requested = "app"
    mode = requested if requested in ("app", "raw", "line") else "app"
    if mode == "raw" and _live_input_unsafe_terminal(env):
        return "line"
    return mode


def _resolve_effective_options(
    *,
    project_defaults: dict[str, Any],
    user_llm: dict[str, Any],
    user_cli: dict[str, Any],
    max_cycles: int | None,
    max_turns: int | None,
) -> dict[str, Any]:
    # ``load_layered_defaults`` intentionally preserves structured blocks.
    # Normalize the documented ``llm: {provider, model, ...}`` form onto the
    # flat lookup surface used below, while keeping legacy top-level values as
    # higher-precedence compatibility aliases.
    nested_llm = project_defaults.get("llm")
    if isinstance(nested_llm, dict):
        project_defaults = {**nested_llm, **project_defaults}
        project_defaults.pop("llm", None)
    if project_defaults.get("provider") is not None:
        provider_source = "project"
        eff_provider = project_defaults.get("provider")
    elif user_llm.get("provider") is not None:
        provider_source = "user"
        eff_provider = user_llm.get("provider")
    else:
        provider_source = "default"
        eff_provider = None
    eff_provider = eff_provider.lower() if isinstance(eff_provider, str) else eff_provider

    active_provider = eff_provider or "anthropic"

    eff_openai_api = _pick_provider_scoped(
        "openai_api",
        active_provider=active_provider,
        provider_source=provider_source,
        project_defaults=project_defaults,
        user_llm=user_llm,
    )
    eff_openai_api = eff_openai_api.lower() if isinstance(eff_openai_api, str) else eff_openai_api
    eff_model = _pick_provider_scoped(
        "model",
        active_provider=active_provider,
        provider_source=provider_source,
        project_defaults=project_defaults,
        user_llm=user_llm,
    )

    max_cycles_value = _pick(max_cycles, project_defaults.get("max_cycles"), user_cli.get("max_cycles"))
    max_turns_value = _pick(max_turns, project_defaults.get("max_turns"), user_cli.get("max_turns"))
    return {
        "provider": eff_provider,
        "model": eff_model,
        "base_url": _pick_provider_scoped(
            "base_url",
            active_provider=active_provider,
            provider_source=provider_source,
            project_defaults=project_defaults,
            user_llm=user_llm,
        ),
        "api_key": _pick_provider_scoped(
            "api_key",
            active_provider=active_provider,
            provider_source=provider_source,
            project_defaults=project_defaults,
            user_llm=user_llm,
        ),
        "openai_api": eff_openai_api,
        "max_cycles": max_cycles_value,
        "max_turns": max_turns_value,
    }


def _add_effective_llm_overrides(overrides: dict[str, Any], eff: dict[str, Any]) -> None:
    """Copy LLM values resolved from arbor setup/project YAML into config overrides.

    ``arbor run`` no longer accepts model/provider flags, but the final
    CoordinatorConfig must still receive the values selected by ``arbor setup``
    or project YAML. Otherwise pydantic defaults would silently fall back to the
    built-in Claude model after preflight has already checked the intended LLM.
    """
    for key in ("provider", "model", "base_url", "api_key", "openai_api"):
        if eff.get(key) is not None:
            overrides[key] = eff[key]


def _validate_effective_options(eff: dict[str, Any]) -> None:
    for key in ("max_cycles", "max_turns"):
        value = eff.get(key)
        if value is None:
            continue
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            typer.secho(
                f"error: --{key.replace('_', '-')} must be a positive integer (got {value!r})",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(code=2)
        if parsed < 1:
            typer.secho(
                f"error: --{key.replace('_', '-')} must be >= 1 (got {parsed})",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(code=2)
        eff[key] = parsed


def _pick(cli_value: Any, project_value: Any, user_value: Any) -> Any:
    if cli_value is not None:
        return cli_value
    if project_value is not None:
        return project_value
    if user_value is not None:
        return user_value
    return None


def _pick_provider_scoped(
    key: str,
    *,
    active_provider: str,
    provider_source: str,
    project_defaults: dict[str, Any],
    user_llm: dict[str, Any],
) -> Any:
    """Pick project/user LLM values without crossing provider boundaries.

    A common footgun is mixing project-level and user-level LLM blocks. Values
    are reused only from the source that selected the provider, or from a source
    that explicitly declares the same provider.
    """
    for source_name, source in (("project", project_defaults), ("user", user_llm)):
        value = source.get(key)
        if value is None:
            continue
        if _source_applies_to_provider(
            source_name=source_name,
            source=source,
            active_provider=active_provider,
            provider_source=provider_source,
        ):
            return value
    return None


def _source_applies_to_provider(
    *,
    source_name: str,
    source: dict[str, Any],
    active_provider: str,
    provider_source: str,
) -> bool:
    if source_name == provider_source:
        return True
    declared = source.get("provider")
    if isinstance(declared, str):
        return _same_provider(declared, active_provider)
    return provider_source == "default"


def _same_provider(a: str | None, b: str | None) -> bool:
    def norm(v: str | None) -> str | None:
        if v is None:
            return None
        v = v.lower()
        if v in ("claude", "anthropic"):
            return "anthropic"
        return v
    return norm(a) == norm(b)
