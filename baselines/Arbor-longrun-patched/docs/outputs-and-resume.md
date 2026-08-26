# Outputs & Resume

Arbor records everything a run produces so you can inspect it, reproduce it, and pick up
where you left off.

## Session artifacts

Each run gets its own session directory, by default under the target project:

```text
<project>/.arbor/sessions/<run_name>/
```

`<run_name>` defaults to a timestamp; set it explicitly with `--run-name`, or relocate the
whole directory with `--workspace-dir`.

Inside you'll find the run's checkpoint (Idea Tree + message history), logs, and the
final report. The exact instruction you launched with is recorded in the session log, and
the fully-resolved settings for the run are saved to
`<run_name>/.coordinator/config_snapshot.yaml` (every config layer merged, secrets
redacted).

!!! tip "Reuse a setup"
    To repeat a study with the same settings in a fresh run, copy that
    `config_snapshot.yaml` to your project root as `arbor.yaml` — Arbor auto-loads it next
    time. See [Preparing a Benchmark → Saving and reusing a setup](preparing-a-benchmark.md#saving-and-reusing-a-setup).

## `REPORT.md`

When a run finishes, Arbor writes a `REPORT.md` — the human-readable write-up of the
study: what was tried, what worked, the evidence behind each conclusion, and the final
result. Use `arbor report` to work with it, and the `/report` slash command during a run
to print artifact paths.

By default Arbor then opens a **read-only Q&A prompt** so you can interrogate the finished
run (disable with `--no-followup`).

## Experiment branches

Every experiment ran on its own git branch in an isolated worktree. Merged improvements
are on trunk; explored-but-unmerged ideas remain as branch refs you can inspect. During a
run, `/branches` lists the explored branch refs and `/tree` prints the Idea Tree snapshot.

## Saving & resuming the planning conversation

The intake chat — what you do at the `arbor` prompt *before* a run launches — is itself
saved every turn, so you can step away and pick it back up. Each conversation lives in its
own directory under the launch folder:

```text
<cwd>/.arbor/conversations/<conv_id>/
```

with the message history (`messages.jsonl`) and a small `meta.json` (title, timestamps,
turn count). Saving is best-effort and never interrupts the chat.

Two ways back in:

- **`arbor --continue`** (`-C`) — reload the most recent *unfinished* conversation and keep
  chatting, like `claude -c`. (`-c` is already `--config`, so the short flag is `-C`.)
- **`/resume`** inside intake — lists saved conversations (tagged `[chat]`) alongside
  launched runs; pick a conversation to keep chatting, or a run to replay the engine.

On resume the prior exchange is replayed to the terminal so you can see what was already
said. Once a conversation launches a run it is recorded as launched and dropped from
`--continue` — the run's own checkpoint (below) takes over.

## Resuming an interrupted run

If a run is interrupted — you stopped it, the machine rebooted, a budget tripped — resume
from its checkpoint instead of starting over:

```bash
arbor --resume --run-name my-study
```

`--resume` reloads the Idea Tree and message history from the existing session and
continues in the same workspace. Combine it with `--run-name` (or `--workspace-dir`) to
point at the session you want to continue.

!!! tip
    Because the Idea Tree is the durable shared state, resuming restores not just *where*
    the run stopped but *what it had learned* — merged improvements, pruned branches, and
    backpropagated insight are all intact.

## Monitoring while running

- **Terminal dashboard** — live cycle status, Idea Tree, and cost.
- **Read-only web monitor** — auto-starts near port `8765` (`--webui-port` to change,
  `--no-webui` to disable).
- **`/cost`** — print token usage at any time.
