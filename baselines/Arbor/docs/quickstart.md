# Quickstart

This guide takes you from a fresh install to a running research session.

## 1. Configure a provider

Run the interactive setup wizard once. It writes your provider, model, and API key to a
user config so you don't repeat them on every run:

```bash
arbor setup
```

!!! tip "First run shortcut"
    If you start a run before configuring anything, Arbor detects the missing config in an
    interactive terminal and walks you through `arbor setup` automatically.

Prefer to do it by hand? Set environment variables instead:

=== "Anthropic"

    ```bash
    export ANTHROPIC_API_KEY=sk-ant-...
    ```

=== "OpenAI"

    ```bash
    export OPENAI_API_KEY=sk-...
    ```

=== "OpenAI-compatible (LiteLLM)"

    ```bash
    export OPENAI_API_KEY=...            # your gateway key
    export OPENAI_BASE_URL=https://your-gateway/v1
    ```

See [Configuration](configuration.md) for the full provider matrix.

## 2. Start a session

The way to use Arbor is to run `arbor` inside your project directory:

```bash
cd my_project
arbor
```

This opens an **intake chat**. You describe your goal in plain language; the intake agent
reads your code and README, confirms the metric and baseline, helps you shape a plan, and
then launches the study once you both agree. From there you stay in the same terminal —
watching progress and steering the run with slash commands.

!!! tip "No project yet? Try the bundled example"
    For a no-API-budget, no-GPU, sub-second run you can watch end-to-end, use the
    [`examples/algotune_knn`](https://github.com/RUC-NLPIR/Arbor/tree/main/examples/algotune_knn)
    task — make a brute-force k-NN solver faster while matching the reference output.
    Run it **outside** your Arbor checkout so experiment worktrees don't touch that repo:

    ```bash
    cp -r examples/algotune_knn /tmp/algotune_knn
    cd /tmp/algotune_knn
    git init -q && git add -A && git commit -qm baseline
    arbor
    ```

!!! tip "Seed the goal up front"
    You can pass your objective as the first argument and still go through intake:

    ```bash
    arbor "maximize dev score without changing eval or data"
    ```

??? note "Headless / scripted runs (CI)"
    To launch without the intake conversation — for benchmarks or CI — skip the chat with
    `--yes` and point at the project explicitly:

    ```bash
    arbor run "improve held-out accuracy" \
      --yes \
      --yes-cwd /path/to/project \
      --config /path/to/project/research_config.yaml
    ```

    For day-to-day use, prefer the interactive `arbor` above.

## 3. Watch it work

While a run is active you get three views:

- **Terminal dashboard** — live status of the current cycle, the Idea Tree, and costs.
- **Read-only web monitor** — auto-starts in your browser near port `8765`
  (disable with `--no-webui`, change with `--webui-port`).
- **`REPORT.md`** — the final write-up, generated when the run finishes.

Inside the dashboard you can steer the run with slash commands such as `/status`, `/tree`,
`/evidence`, `/cost`, `/pause`, and `/resume`. See the [CLI reference](cli.md#interactive-slash-commands).

## 4. Read the results

When the run completes, Arbor writes a `REPORT.md` and opens an optional read-only Q&A
prompt so you can interrogate the finished study (disable with `--no-followup`). All
artifacts — the Idea Tree, checkpoints, logs, and per-experiment branches — live under
`<project>/.arbor/sessions/<run_name>/`.

## Where to go next

<div class="grid cards" markdown>

-   :material-book-open-variant: **Preparing a Benchmark**

    Wire up an eval command and protect your data so Arbor can iterate safely.

    [:octicons-arrow-right-24: Preparing a Benchmark](preparing-a-benchmark.md)

-   :material-sitemap: **How It Works**

    The arbor cycle, the Idea Tree, and held-out discipline.

    [:octicons-arrow-right-24: How It Works](how-it-works.md)

</div>
