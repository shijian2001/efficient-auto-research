# CLI Reference

Arbor installs the `arbor` command (plus a few lower-level entry points). This page is the
complete reference.

## Commands

| Command | What it does |
| --- | --- |
| `arbor` | With no subcommand, behaves like `arbor run` — starts an interactive session in the current directory. |
| `arbor run` | Start an AI-powered research session. |
| `arbor setup` | Interactive wizard to write your provider, model, and API key. |
| `arbor config` | Inspect and manage stored configuration. |
| `arbor doctor` | Diagnose your environment (PATH, Python, git, API keys). |
| `arbor report` | Work with a finished run's report. |
| `arbor idea-check` | Novelty / prior-art check for a single idea against the public alphaXiv API. |
| `arbor version` | Print the installed version. |

!!! tip
    Running `arbor` (or `arbor --cwd .`) with no subcommand is equivalent to `arbor run`.

## `arbor run`

```bash
arbor run [INSTRUCTION] [OPTIONS]
```

`INSTRUCTION` is an optional research-goal seed (e.g. `"maximize dev score without
changing eval or data"`). Omit it to start with the intake chat.

### Default flow

1. Open an interactive chat with the intake agent. Read/discuss requests stay in scoped,
  read-only discussion mode; launch planning is enabled only for an optimization/run goal.
2. The agent confirms which project directory to work on. `--cwd` is the default boundary;
  external paths must be named explicitly and are re-authorized after chat resume.
3. The agent stages an exact plan and shows it to you. A later, explicit confirmation launches
  that same staged plan; visible questions never execute hidden tool calls.
4. You confirm the fully resolved research contract shown in the terminal.
5. A quick preflight runs against the chosen project.
6. The coordinator runs to completion and writes `REPORT.md`.

### Options

| Option | Description |
| --- | --- |
| `--cwd PATH` | Project directory hint. Intake verifies/adjusts it unless `--yes` is used. Default `.`. |
| `--config, -c PATH` | Project YAML config. Defaults to `research_config.yaml` / `arbor.yaml` / `autoresearch.yaml` in the target project. |
| `--max-cycles N` | Max completed/skipped/failed idea experiments before finalizing. |
| `--max-turns N` | Hard cap on coordinator ReAct turns — a cost/runaway safety valve. |
| `--intake-max-turns N` | Max internal agent turns for each intake message (default `30`). |
| `--run-name NAME` | Session name under `.arbor/sessions/`. Defaults to a timestamp. |
| `--resume` | Resume an interrupted run from its checkpoint in the existing workspace/session. |
| `--workspace-dir PATH` | Session/artifact directory override. Default `<target>/.arbor/sessions/<run_name>`. |
| `--verbose, -v` | Show lower-level coordinator logs. |
| `--yes-cwd PATH` | Target project directory when `--yes` skips intake. Required with `--yes`. |
| `--yes, -y` | Skip intake chat and launch directly from instruction + `--yes-cwd`. |
| `--no-dashboard-input` | Disable live terminal input; prompts/review gates auto-continue after timeout. |
| `--followup / --no-followup` | After `REPORT.md`, open a read-only Q&A prompt about the finished run (default on). |
| `--verbose-preflight` | Print successful preflight checks too (default shows only failures/warnings). |
| `--webui-port N` | Read-only browser monitor port. Default auto-starts near `8765` for interactive runs. |
| `--no-webui` | Do not start the read-only browser monitor. |
| `--interaction-mode, --mode MODE` | Human-in-loop mode: `auto`, `direction`, `review`, `collaborative`. |
| `--allow-non-base-branch` | Allow starting from the current non-`main` branch. Useful for dev, risky for benchmarks. |

### Examples

```bash
# Interactive: chat with intake, then run in the current directory
arbor run

# Seed a goal, still go through intake
arbor run "improve held-out accuracy"

# Headless: skip the chat entirely
arbor run "maximize the competition metric" \
  --yes --yes-cwd /path/to/project \
  --config /path/to/project/research_config.yaml

# Ask for approval before each idea is run
arbor run --mode review

# Resume an interrupted session
arbor run --resume --run-name my-study
```

## Interactive slash commands

While a run is active, type these in the terminal dashboard. A short menu pops up as you
type `/`; `/help` lists them all.

| Command | Action |
| --- | --- |
| `/help` | Show all dashboard commands. |
| `/ask <question>` | Ask the read-only companion a question about the run. |
| `/steer <message>` | Inject a message into the research agent. |
| `/mode ask\|research` | Set the default target for plain input. |
| `/status` | Print run status. |
| `/skill <name...>` | Ask the agent to load the named skill(s). |
| `/tree` | Print the current Idea Tree snapshot. |
| `/evidence` | Show score/baseline evidence. |
| `/reply` | Expand/collapse the full companion answer (or press ++tab++). |
| `/chart` | Toggle the live progress chart. |
| `/branches` | Show explored branch refs. |
| `/cost` | Print token usage. |
| `/pause` | Ask the agent to pause after the current step. |
| `/resume` | Resume after `/pause`. |
| `/report` | Show session/report artifact paths. |
| `/abort` (or `/quit`) | Abort the run. |

## `arbor idea-check`

Run a quick novelty / prior-art check on a single research idea, without
starting a full run. It dispatches one SearchAgent that surveys the public
[alphaXiv](https://www.alphaxiv.org) literature and prints a lightweight
verdict: a summary of what's been done, the closest related papers, a novelty
assessment (`novel` / `partial-overlap` / `prior-art-exists`), and overlap
risks.

```bash
# Built in on Python >= 3.12 — no extra install needed.
arbor idea-check "Use entity-relation scratchpads to improve multi-hop QA"
arbor idea-check "tree search over plans for code generation" --json
```

Zero-config: no search endpoint or alphaXiv API key is required — it queries
alphaXiv's public endpoints directly. Reuses your existing Arbor LLM
credentials (`arbor login` / API key).

| Option | What it does |
| --- | --- |
| `--focus TEXT` | Optional focus directive (e.g. `prefer arxiv 2024`). |
| `--model TEXT` | Override the model used for the check. |
| `--json` | Print the raw SearchAgent JSON instead of rendered Markdown. |
| `--cwd PATH` | Working directory (default: current directory). |

The same backend powers the coordinator's in-run novelty checks — see
[Configuration](configuration.md) for `search.builtin_backend` and
`search.auto_search_on_add`.

## Other entry points

For advanced/low-level use, Arbor also installs:

| Command | Purpose |
| --- | --- |
| `executor` | Run a single executor directly. |
| `coordinator` | Run the coordinator directly. |
| `run-research` | Lower-level run entry point. |
| `review-research` | Review a finished run. |

Most users only need `arbor`.
