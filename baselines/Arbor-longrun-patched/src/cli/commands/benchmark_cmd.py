"""`arbor benchmark` — verify, list, scaffold, and collect Task Packs in the zoo.

Subcommands:

* ``arbor benchmark verify <pack-dir>`` — structurally check a benchmark folder
  (README + PROVENANCE + eval entrypoint present). Does not run the eval.
* ``arbor benchmark list [zoo-dir]`` — print a plain index of packs (not a ranked
  leaderboard).
* ``arbor benchmark scaffold <dir>`` — write the measurement plumbing (light) or a
  full zoo benchmark (zoo) into an existing local directory.
* ``arbor benchmark add "<request>" | <url>`` — turn a one-line request into a runnable
  draft task. A natural-language request is handled end-to-end by an agent: it finds the
  dataset/benchmark, asks (on a TTY) which dataset to use and where the baseline comes from
  (harvest an existing one / implement the method you described / find one online), acquires
  the data, and brings up a runnable draft (baseline + eval + README + PROVENANCE) — without
  force-running the eval. A bare URL/HF spec skips discovery and just acquires + scaffolds;
  ``--bringup`` also brings up its baseline.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import typer

from ...zoo import (
    VerifyResult,
    bringup,
    collect,
    discover,
    discover_packs,
    select_acquirer,
    verify_pack,
)

benchmark_app = typer.Typer(
    name="benchmark",
    help="Verify and list Task Packs in the arbor-zoo benchmark format.",
    no_args_is_help=True,
)


def _render(r: VerifyResult) -> None:
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


def _user_runner(*, with_search: bool, ask_user: bool = False):
    """Build an agent runner that uses the user's configured provider (~/.arbor/config.yaml),
    so the collection agents inherit the same LLM as `arbor run` (e.g. openai-oauth/gpt-5.5).

    ``ask_user`` adds a console AskUser tool so the agent can put a genuinely human decision
    (e.g. which implementation is the baseline) to the user; enable it only on an interactive
    terminal."""
    from ...zoo import real_agent_runner
    from ..user_config import llm_defaults

    llm = llm_defaults()
    return real_agent_runner(
        with_search=with_search, ask_user=ask_user,
        provider=llm.get("provider"), model=llm.get("model"),
        api_key=llm.get("api_key"), base_url=llm.get("base_url"),
    )


@benchmark_app.command("verify")
def verify_command(
    pack_dir: Path = typer.Argument(
        ...,
        help="Path to the benchmark directory (with README.md + eval.sh/eval.py).",
    ),
) -> None:
    """Structurally verify a benchmark folder (does not run the eval). Exits 1 on failure."""
    target = pack_dir.resolve()
    if not target.is_dir():
        typer.secho(f"error: not a directory: {target}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)

    typer.echo(f"verifying {target.name} …")
    results = verify_pack(target)
    for r in results:
        _render(r)

    fails = [r for r in results if r.status == "fail"]
    if fails:
        typer.secho(
            f"\n{len(fails)} check(s) failed — see the hints above.",
            fg=typer.colors.RED, err=True,
        )
        raise typer.Exit(code=1)
    typer.secho("\nlooks good — all structural checks pass.", fg=typer.colors.GREEN)


@benchmark_app.command("list")
def list_command(
    zoo_dir: Path = typer.Argument(
        Path("arbor-zoo"),
        help="The zoo directory to index (default: ./arbor-zoo).",
    ),
) -> None:
    """List the packs in a zoo directory (a plain index, not a leaderboard)."""
    target = zoo_dir.resolve()
    packs = discover_packs(target)
    if not packs:
        typer.echo(f"(no packs found in {target})")
        raise typer.Exit(code=0)
    typer.echo(f"# packs in {target}\n")
    width = max(len(p.name) for p in packs)
    for p in packs:
        typer.echo(f"  {p.name.ljust(width)}  {p.description}")


@benchmark_app.command("scaffold")
def scaffold_command(
    target_dir: Path = typer.Argument(..., help="Directory to scaffold (created if missing)."),
    name: str | None = typer.Option(None, "--name", help="Benchmark name (default: dir name)."),
    direction: str = typer.Option("maximize", "--direction", help="maximize | minimize."),
    style: str = typer.Option("light", "--style", help="light | zoo."),
    split_kind: str = typer.Option("seed_range", "--split-kind", help="seed_range | path."),
    entrypoint: str = typer.Option("eval.py", "--entrypoint", help="eval.py | eval.sh."),
    edit: list[str] = typer.Option(["solution.py"], "--edit", help="Editable file/glob (repeatable)."),
    git_init: bool = typer.Option(False, "--git-init", help="git init + baseline commit."),
) -> None:
    """Scaffold the Arbor-ready reference folder (light) or a full zoo benchmark (zoo)."""
    from ...zoo import scaffold_benchmark

    target = target_dir.resolve()
    pack_name = name or target.name
    try:
        res = scaffold_benchmark(
            target, name=pack_name, metric_direction=direction, style=style,
            split_kind=split_kind, eval_entrypoint=entrypoint, edit=edit,
        )
    except ValueError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc

    if git_init and not (target / ".git").exists():
        subprocess.run(["git", "init"], cwd=target, check=False)
        subprocess.run(["git", "add", "-A"], cwd=target, check=False)
        # Ephemeral identity so the commit succeeds without a global git user.
        subprocess.run(
            ["git", "-c", "user.email=arbor@localhost", "-c", "user.name=Arbor",
             "commit", "-m", "baseline: scaffold Arbor benchmark structure"],
            cwd=target, check=False,
        )

    typer.echo(f"scaffolded {target.name} ({style}) …")
    for f in res.created:
        typer.secho(f"  created  {f}", fg=typer.colors.GREEN)
    for f in res.skipped:
        typer.secho(f"  skipped  {f} (exists)", fg=typer.colors.YELLOW)
    for r in res.verify:
        _render(r)
    if res.next_steps:
        typer.echo("\nnext steps:")
        for s in res.next_steps:
            typer.echo(f"  - {s}")


@benchmark_app.command("add")
def add_command(
    spec: str = typer.Argument(
        ...,
        help="A natural-language query, a git repo URL (optionally `url@commit`), or a HF "
             "dataset (`hf:<id>`). A query is resolved by the discovery agent.",
    ),
    name: str | None = typer.Option(
        None, "--name", "-n",
        help="Pack name (the arbor-zoo/<name> folder). Optional for a query (discovery suggests one).",
    ),
    dest: Path = typer.Option(
        Path("arbor-zoo"), "--dest",
        help="Where to write the draft pack (default: ./arbor-zoo).",
    ),
    assume_yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip the confirmation prompt for a discovered benchmark.",
    ),
    do_bringup: bool = typer.Option(
        False, "--bringup",
        help="Force the bring-up agent (implement/wire the baseline + write the eval). "
             "A natural-language query runs bring-up by default; use this to also trigger it "
             "for a bare URL/HF spec. Needs a configured LLM provider / API key.",
    ),
    max_turns: int = typer.Option(
        100, "--max-turns", help="Agent turn budget (discovery / bring-up).",
    ),
) -> None:
    """Turn a one-line request into a runnable draft task.

    A **natural-language request** is handled by an agent end-to-end: it searches GitHub /
    HuggingFace / arXiv, and — on an interactive terminal — asks you which dataset to use and
    where the baseline should come from (harvest an existing one, implement the method you
    described, or find one online). It then acquires the data and brings up a *runnable draft*
    (baseline + eval + README + PROVENANCE). It does NOT force-run the eval — a real run may
    need your served model / API key. A bare URL/HF spec skips discovery and just acquires +
    scaffolds; add ``--bringup`` to also bring up its baseline. Acceptance stays human.
    """
    import asyncio
    import sys

    interactive = sys.stdin.isatty()
    request = spec                          # the user's original words (shapes an implemented baseline)
    from_query = select_acquirer(spec) is None
    baseline_plan: dict = {}

    # ── Natural-language request → discovery agent → a chosen source ──────────
    if from_query:
        import tempfile

        typer.secho(f"searching for a benchmark matching: {spec!r} …", fg=typer.colors.CYAN)
        try:
            disc = asyncio.run(discover(
                spec, run_agent=_user_runner(with_search=True, ask_user=interactive),
                work_dir=Path(tempfile.mkdtemp(prefix="arbor-discover-")),
                max_turns=max_turns,
            ))
        except Exception as exc:  # noqa: BLE001
            typer.secho(f"  discovery could not start: {exc}", fg=typer.colors.RED, err=True)
            typer.echo("  (configure a provider with `arbor setup` / set your API key)")
            raise typer.Exit(code=1) from exc
        for note in disc.notes:
            typer.echo(f"  • {note}")
        if not disc.ok or not disc.url:
            typer.secho("no suitable benchmark found — give a specific repo URL instead.",
                        fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1)
        choice = disc.choice or {}
        baseline_plan = disc.baseline_plan
        typer.secho(f"\nchosen: {disc.name}  →  {disc.url}", fg=typer.colors.GREEN)
        typer.echo(f"  metric:   {choice.get('metric', '?')}")
        typer.echo(f"  baseline: {choice.get('baseline', '?')}")
        if baseline_plan:
            typer.echo(f"  plan:     baseline via {baseline_plan.get('source', '?')} — "
                       f"{baseline_plan.get('detail', '')}")
        typer.echo(f"  why:      {choice.get('why', '?')}")
        if not assume_yes and not typer.confirm("\nacquire this benchmark?", default=True):
            raise typer.Exit(code=0)
        spec = disc.url
        name = name or disc.name

    if not name:
        typer.secho("error: --name is required (could not infer one).",
                    fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)

    typer.echo(f"collecting {name} from {spec} …")
    try:
        result = collect(spec, name=name, dest_root=dest.resolve())
    except RuntimeError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    for note in result.notes:
        typer.echo(f"  • {note}")

    fails = [r for r in result.verify_results if r.status == "fail"]
    if result.draft_pack_dir:
        typer.secho(f"\ndraft pack: {result.draft_pack_dir}", fg=typer.colors.GREEN)
    typer.echo(f"structural verify: {len(fails)} fail(s) "
               f"(eval not run — the draft eval is a stub)")

    # A query runs bring-up by default (producing a runnable draft is the whole point); a bare
    # URL/HF spec only brings up when asked with --bringup.
    if (from_query or do_bringup) and result.draft_pack_dir:
        materials = result.acquired.materials_dir if result.acquired else None
        typer.secho("\nbringing up the baseline (agent) …", fg=typer.colors.CYAN)
        try:
            br = asyncio.run(bringup(
                result.draft_pack_dir,
                run_agent=_user_runner(with_search=True, ask_user=interactive),
                materials_dir=materials,
                instruction=request if from_query else "",
                baseline_plan=baseline_plan,
                max_turns=max_turns,
            ))
        except Exception as exc:  # noqa: BLE001 — surface provider/setup errors clearly
            typer.secho(f"  bring-up could not start: {exc}", fg=typer.colors.RED, err=True)
            typer.echo("  (configure a provider with `arbor setup` / set your API key, then retry)")
            raise typer.Exit(code=1) from exc
        for note in br.notes:
            typer.echo(f"  • {note}")
        if br.ran:
            typer.secho(f"  bring-up {'ok' if br.ok else 'incomplete'} — eval ran "
                        f"(dev score: {br.dev_score})",
                        fg=typer.colors.GREEN if br.ok else typer.colors.YELLOW)
        else:
            typer.secho(f"  {'runnable draft ready' if br.ok else 'bring-up incomplete'} — "
                        f"eval not run here (needs your model / API key)",
                        fg=typer.colors.GREEN if br.ok else typer.colors.YELLOW)

    typer.secho("\nstill to do (drafting is automated, acceptance is not):",
                fg=typer.colors.YELLOW)
    for step in result.pending:
        typer.echo(f"  - {step}")
