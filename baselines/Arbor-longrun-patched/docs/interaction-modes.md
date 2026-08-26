# Interaction Modes (Human-in-the-Loop)

Arbor runs fully autonomously by default, but you decide how much oversight you want. The
**interaction mode** controls when — if ever — the agent pauses to consult you, and a set
of live controls let you steer a run as it happens.

## The four modes

| Mode | Behaviour |
| --- | --- |
| `auto` | Fully autonomous. The agent never pauses for input. |
| `direction` | The agent asks **where to explore** at key junctions. |
| `review` | The agent asks you to **approve or edit ideas** before running them. |
| `collaborative` | Both the `direction` and `review` gates are active. |

### Setting the mode

Per run, on the command line (alias `--mode`):

```bash
arbor --mode review
arbor --interaction-mode collaborative
```

Or durably in config:

```yaml title="research_config.yaml"
ui:
  interaction_mode: auto      # auto | direction | review | collaborative
```

As always, the CLI flag overrides the config value. See
[Configuration → When settings disagree: precedence](configuration.md#when-settings-disagree-precedence).

## What a gate looks like

When a gate triggers, the agent pauses and asks for your input — in the terminal
dashboard and, for interactive runs, in the [Web UI](web-ui.md). In `review` mode, for
example, you can approve a proposed idea as-is, edit it, or redirect; in `direction` mode
you nudge which part of the tree to expand next.

!!! tip "Unattended runs still make progress"
    Pass `--no-dashboard-input` and gates **auto-continue after a timeout** instead of
    blocking forever. This lets you run a `review`/`collaborative` study unattended: it
    pauses briefly for input, then proceeds on its own if you're away.

## Steering a run live

Independent of the mode, you can always influence an active run from the terminal
dashboard (and the interactive Web UI) using [slash commands](cli.md#interactive-slash-commands):

| Command | Use |
| --- | --- |
| `/steer <message>` | Inject guidance directly into the research agent. |
| `/ask <question>` | Ask the read-only companion about the run (doesn't change it). |
| `/skill <name...>` | Ask the agent to load a [Skill](skills.md) on demand. |
| `/pause` / `/resume` | Pause after the current step, then resume. |
| `/tree`, `/evidence`, `/branches` | Inspect state before deciding how to steer. |
| `/abort` | Stop the run. |

## Choosing a mode

| You want… | Use |
| --- | --- |
| Maximum autonomy / benchmarks | `auto` |
| To keep the agent on a research direction you care about | `direction` |
| A check on each hypothesis before compute is spent | `review` |
| Close collaboration on a hard problem | `collaborative` |
| Hands-off but with light supervision | any mode + `--no-dashboard-input` |

For how gates fit into the search loop, see
[How It Works → Human-in-the-loop](how-it-works.md#human-in-the-loop).
