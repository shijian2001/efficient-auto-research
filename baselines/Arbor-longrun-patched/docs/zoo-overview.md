# Benchmark Zoo

Arbor's workflow is to take a task that prints a score and iteratively edit the code, run
the eval, and keep the changes that improve the score. The **benchmark zoo** is a collection
of such tasks, each packaged in one standard format so it can be handed directly to Arbor to
optimize. It lives in [`arbor-zoo/`](https://github.com/RUC-NLPIR/Arbor/tree/main/arbor-zoo),
one folder per benchmark.

## What it's for

- **Ready-to-run optimization tasks.** Each benchmark ships an eval script and a baseline;
  point Arbor at it and it begins optimizing.
- **Onboard your own task.** If you have code but no runnable eval, one command adds the eval
  scaffolding.
- **Collect new ones (in progress).** Describe what you want in one line — name a work
  ("get me the datasets WebThinker uses") or a goal ("I want to climb GPQA with a
  self-consistency method"). An agent finds the dataset/benchmark, asks you which dataset to
  use and where the baseline should come from (harvest an existing one, implement the method
  you described, or find one online), acquires the data, and brings up a runnable draft. You
  can also point it straight at a repo URL.

## What a benchmark contains

Each benchmark is a directory with three parts:

- a **README** — the task description: what the task is, the metric, and what Arbor may edit;
  read by Arbor during intake;
- **baseline code** — the starting point for optimization, and the only part Arbor may edit
  (e.g. `solution.py`);
- an **eval script** — prints one `score:` line when run; it is protected, so Arbor cannot
  modify it.

Arbor's loop is: edit the baseline → run the eval → keep the change if the score improved,
and repeat.

## Entry points

| Purpose | Command |
| --- | --- |
| List the benchmarks | `arbor benchmark list` |
| Run Arbor on a benchmark | copy it out of the repo, `git init`, run `arbor` inside it |
| Verify a benchmark's structure | `arbor benchmark verify <dir>` |
| Make your code a runnable benchmark | `arbor benchmark scaffold <dir>` |
| Find & build a benchmark from a request | `arbor benchmark add "<request>"` (or a repo URL) |

Running one:

```bash
cp -r arbor-zoo/algotune_knn /tmp/algotune_knn   # copy out of the Arbor repo
cd /tmp/algotune_knn && git init -q && git add -A && git commit -qm baseline
arbor                                             # confirm the task, then it iterates
```

## Status

- **Available:** the format, `verify`, `list`, `scaffold`, the `add` spine, and the first
  example benchmark, `algotune_knn`.
- **In progress:** strengthening `add` — from a one-line request, find the dataset, ask which
  one and where the baseline comes from, and bring up a runnable draft — and adding more
  benchmarks.

For the exact format, see the [format reference](zoo.md); for the wider plan, see the
[roadmap](roadmap.md).
