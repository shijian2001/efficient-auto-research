# Web UI & Monitoring

Every interactive Arbor run exposes two live views of the same study: a **terminal
dashboard** and a **browser monitor (Web UI)**. Both read from the same event stream, so
they always agree.

## The terminal dashboard

When you start a run in a terminal, Arbor renders a live dashboard showing the current
cycle, the Idea Tree, costs, and the agent's thinking/tool stream. You interact with it via
[slash commands](cli.md#interactive-slash-commands) — `/status`, `/tree`, `/evidence`,
`/steer`, `/pause`, and so on.

Disable live terminal input with `--no-dashboard-input` (prompts and review gates then
auto-continue after a timeout — useful for unattended runs).

## The browser monitor (Web UI)

For interactive runs, Arbor also starts a small web server that mirrors the run to your
browser. It renders a snapshot of the run state plus the live thinking/tool stream over
Server-Sent Events, so you can watch progress on a second screen or share a link with
collaborators on the same network.

The URL is printed in the dashboard header once the server binds, e.g.
`http://127.0.0.1:8765`.

### Ports

| Behaviour | Detail |
| --- | --- |
| **Default port** | `8765`. |
| **Auto-roll** | If `8765` is busy, Arbor walks forward up to 10 ports (`8765`–`8774`) until one binds. |
| **Pick a port** | `--webui-port N` (or `ui.webui_port` in config) sets an explicit port. An explicit port is tried exactly once — a busy port is surfaced rather than silently moved. |
| **Disable** | `--no-webui` skips the browser monitor entirely. |

```bash
arbor --webui-port 9000      # serve the monitor on :9000
arbor --no-webui             # no browser monitor at all
```

### Read-only vs. interactive

By default the Web UI is **read-only** — the browser only observes. In an interactive run
(a TTY, without `--no-dashboard-input`) the monitor also becomes **interactive**, letting
you from the browser:

- **Ask** the read-only companion a question about the run,
- **Steer** the research agent by injecting a message, and
- **Answer** human-in-the-loop gates (e.g. approve/edit ideas in `review` mode).

Interactive browser actions are protected by a per-run token in the URL, so only someone
with the printed link can drive the run. If you want a purely passive monitor, start the
run with `--no-dashboard-input` (or simply use `--no-webui`).

!!! note "Headless / scripted runs"
    Non-interactive runs (no TTY, or launched with `--yes`) don't need a browser monitor.
    Use `--no-webui` to skip it, and rely on `REPORT.md` and the session logs instead.

## Which view should I use?

| You want to… | Use |
| --- | --- |
| Drive the run, type commands, approve ideas | Terminal dashboard (slash commands) |
| Watch progress on a second screen / share a link | Web UI |
| Run unattended in CI or a script | `--no-webui --no-dashboard-input` |

Both views are optional conveniences layered on top of the same durable artifacts — the
Idea Tree, checkpoints, and `REPORT.md`. See [Outputs & Resume](outputs-and-resume.md).
