# Preparing a Benchmark

You don't write a config to point Arbor at a task. There is really only **one hard
requirement**: a repo where Arbor can **run something to get a score**. Everything else —
what the metric means, what's off-limits, how ambitious to be — you settle in a short
**intake chat** when you launch `arbor`. No hand-written YAML, no eval contract to author,
and **no README required**.

!!! tip "Prefer to learn by example?"
    [`examples/algotune_knn`](https://github.com/RUC-NLPIR/Arbor/tree/main/examples/algotune_knn)
    is a tiny, runnable benchmark that already follows everything below — an editable
    `solution.py`, a protected `eval.sh` that prints `score:`, and disjoint dev/test
    seeds. It's the fastest way to see the shape of a benchmark before wiring up your own.

## 1. A scorable baseline repo

Put your code and data in one directory — typically the repo you already have:

```text
my_task/
├── data/            # datasets and any fixed inputs
├── eval.sh          # scores a candidate and prints the metric
└── train.py         # your starting-point code (Arbor edits this)
```

The one thing Arbor needs is a command that **prints a metric on a line it can read**. Your
`eval.sh` (or `python eval.py`, `make eval`, …) should emit something like:

```text
score: 0.8123
```

A simple working baseline already in the repo is ideal — it gives Arbor a number to beat
and confirms the eval actually runs.

!!! tip "Only have code? Intake can plan the eval setup with you"
    You don't need an eval script — or even a dev/test split — *before* you start. If your
    repo is just code, launch `arbor` anyway: in the intake chat the agent asks what
    "better" means, then stages a plan whose first coordinator task can **scaffold a minimal
    eval** (and, if you want, carve a **dev/held-out split**) after you confirm. No held-out
    set and don't want one? You can
    iterate on a single split — the agent will just note that the final score has no
    held-out guard. See [Describe the task](#2-describe-the-task-readme-or-just-tell-the-cli).

!!! tip "Let Arbor do the plumbing after approval"
    You don't have to pre-initialize git, `chmod +x` your script, or run the eval yourself.
    Intake itself is read-only: it inspects files inside the approved scope and stages the
    contract. After you confirm, deterministic preflight and the coordinator handle setup
    and evaluation work; intake never runs shell commands behind a displayed question.

## 2. Describe the task — README *or* just tell the CLI

Arbor needs a plain-language picture of the task. You can supply it **either way**:

- **In the intake chat** — the simplest path. Launch `arbor`, and the agent reads your
  eval script and code, proposes the metric, baseline, goal, and constraints it inferred,
  and asks you to confirm or correct them. You fill any gaps by just talking. No file to
  write.
- **In a README** (optional) — if your repo already has one, the agent reads it the way a
  person would and pre-fills the plan from it, so there's less to confirm. Handy when you
  run the same task often or hand it to a colleague.

Either way, the picture covers four things — whether they come from a README or from your
answers in chat:

- **The task** — what the project is and what a solution looks like.
- **The metric** — which number is being optimized and whether higher or lower is better
  (e.g. "maximize accuracy printed by `bash eval.sh`").
- **The goal** — how ambitious this run is ("beat the baseline", "get above 60%", or
  "push as high as possible").
- **What's off-limits** — anything Arbor must not modify, such as `data/` or the eval
  script itself.

There's no special format or required fields. The clearer the description — typed or
spoken — the better the agent's first plan.

## 3. Launch and confirm

From inside the repo, start the interactive CLI:

```bash
cd my_task
arbor
```

In the **intake chat**, the agent inspects the repo (and your README, if any), states the
metric, baseline, goal, and constraints it inferred, and asks you to confirm or correct
them in one shot. Say "go" and it launches the study — proposing hypotheses, editing your
code, running real experiments, and keeping only the changes that improve the held-out
score.

That's the whole setup. Everything Arbor needs — how to evaluate, what to protect, what
counts as "better" — comes from your repo plus the short confirmation, not from a config
file you maintain.

!!! note "Held-out discipline"
    Arbor iterates on a **dev** signal but only keeps a change if it improves a **held-out**
    metric by a margin. That is what prevents overfitting to the iteration signal. See
    [How It Works → Evaluation discipline](how-it-works.md#evaluation-discipline).

## Saving and reusing a setup

Nothing you tell the intake agent is thrown away. Every run records both the **instruction**
(your refined goal) and the **fully-resolved settings** for that run, so you can pick up or
repeat work later without retyping anything.

- **Continue the same study** — each run is checkpointed under
  `<project>/.arbor/sessions/<run_name>/`. Just launch `arbor` again in the project and
  pick the past run from the resume list (or `arbor --resume --run-name <name>`). This
  restores the Idea Tree, history, and the original instruction. See
  [Outputs & Resume](outputs-and-resume.md).
- **Repeat with the same settings, fresh** — every run writes its resolved config to
  `<run_name>/.coordinator/config_snapshot.yaml` (all layers merged, secrets redacted).
  Copy it to your project root as `arbor.yaml` (or `research_config.yaml`) and Arbor
  auto-loads it on the next run, so the budget, protected paths, and provider settings
  carry over. See [Configuration](configuration.md).
- **Set defaults once for every project** — run `arbor setup` to write
  `~/.arbor/config.yaml`; those become your global CLI defaults.
- **Re-run the exact same goal unattended** — pass the instruction headlessly and skip the
  chat entirely:

    ```bash
    arbor run "maximize dev score from bash eval.sh; don't touch data/ or eval.sh" \
      --yes --yes-cwd ./my_task
    ```

## Going further

For a **one-off** study, the steps above are all you need. If you run the **same kind of
benchmark repeatedly** — and want to pin the exact eval contract, protected paths, budget,
and domain guidance so every run is identical — capture them once in a
[plugin](plugins.md). Arbor ships one for Kaggle / MLE-bench (`mle_kaggle`) as a worked
example.
