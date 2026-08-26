# Benchmark Zoo — format reference

This page is the exact format for a benchmark folder and what `arbor benchmark verify`
checks. For the big picture, see the [overview](zoo-overview.md).

The format is **documentation-first**: a benchmark is a well-documented folder. The README
is plain prose that Arbor reads at intake — there is no YAML manifest to fill in.

## What a benchmark folder contains

Each `arbor-zoo/<name>/` holds four things:

| File / dir | Role | For |
| --- | --- | --- |
| `README.md` | What the task is, the metric, what Arbor may edit, how dev/test differ — in plain language. | Arbor (and humans) |
| baseline code | The editable baseline — `solution.py`, or a whole set of files. | Arbor edits |
| `eval.sh` *or* `eval.py` | Protected eval entrypoint. `bash eval.sh dev\|test` (or `python eval.py --split …`) prints one `score: <float>` line. | protected |
| `PROVENANCE.md` | Source, setup, how the baseline works, contamination, caveats. | humans |
| `data/`, `task.py`, … | Any data / ground-truth the eval needs (protected). | — |

Folders whose name starts with `_` (e.g. `_template`) are scaffolding and are skipped.

### `README.md` — the task description

The README is what Arbor reads to understand the task, the same way its intake reads any
repository. There is no fixed template; a description usually covers four things:

1. **The task** — what it is and what a solution looks like.
2. **The metric** — what the eval prints (one `score:` line) and whether higher or lower is
   better.
3. **What Arbor may edit** — the baseline file(s); everything else (the eval script,
   reference implementation, data) is protected.
4. **Dev / test** — how the two are split, so the held-out set is clear (disjoint seeds, or
   `data/dev/` vs `data/test/`).

The format contains **no fixed baseline number**: the same baseline gives different scores on
different hardware/models, so the baseline is described in PROVENANCE rather than pinned as a
value.

### `PROVENANCE.md` — the human card

Required sections (the verifier checks they're present): **Source**, **Setup & environment**,
**Baseline**, **Contamination assessment**, **Caveats**. This is where source, license, how the
baseline works (and how much its score varies), and the held-out reasoning are written down for
a maintainer to read.

## What `arbor benchmark verify` checks

`verify` is a light **structural** check — a completeness lint, not a correctness gate. It does
**not run the eval** (a baseline's score isn't universal). It checks:

- a `README.md` is present and non-empty;
- a `PROVENANCE.md` is present with its required sections;
- an eval entrypoint (`eval.sh` or `eval.py`) is present.

```bash
arbor benchmark verify arbor-zoo/<name>   # exits non-zero if a piece is missing
arbor benchmark list arbor-zoo            # index the benchmarks
```

Whether dev/test are *truly* held out and what the baseline really does are stated in
PROVENANCE prose and judged by a human — not machine-enforced.

## Run Arbor on a benchmark

Arbor runs experiments in git worktrees off the repo root, so work from a copy **outside** the
Arbor checkout:

```bash
cp -r arbor-zoo/algotune_knn /tmp/algotune_knn
cd /tmp/algotune_knn
git init -q && git add -A && git commit -qm baseline
arbor   # Arbor reads the README, confirms the task, then iterates
```

## Add a benchmark

1. Scaffold the structure: `arbor benchmark scaffold arbor-zoo/<name> --style zoo`. This writes
   an eval stub, a `solution.py` placeholder, a natural-language `README.md`, and a
   `PROVENANCE.md` — never the solution itself.
2. Fill in the baseline (`solution.py`), the eval (`eval.py`/`eval.sh`), the README (for Arbor),
   and PROVENANCE (for humans).
3. Run `arbor benchmark verify arbor-zoo/<name>` until it exits 0, then a maintainer accepts it.
   Drafting may be automated; **acceptance is a human step**.

For an end-to-end example, see [`arbor-zoo/algotune_knn`](https://github.com/RUC-NLPIR/Arbor/tree/main/arbor-zoo/algotune_knn).
