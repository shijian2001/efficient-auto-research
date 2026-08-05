# AlgoTune-style mini benchmark: brute-force k-NN speedup

A tiny, self-contained example task for Arbor, modelled on
[AlgoTune](https://algotune.io/) (NeurIPS 2025). It needs **no API key and no
GPU**, runs in **well under a second on CPU**, and is fully **deterministic** —
ideal as a smoke test or worked example for the
"edit code → run eval → keep what improves the held-out score" loop.

## The task

Given a database of points and a batch of query points, return the indices of
each query's **k nearest neighbours** (Euclidean distance). The reference
implementation is intentionally naive — full pairwise distances followed by a
full sort. The goal is to compute the **same** neighbours **faster**.

This mirrors AlgoTune's structure: every task is one editable solver plus three
fixed pieces — a problem generator, a correctness verifier, and a reference
implementation that doubles as the speed baseline.

## The metric

`bash eval.sh` prints a single machine-readable line:

```
score: <speedup>
```

where `speedup = median(reference_time) / median(solution_time)`, measured on a
held-out set of instances after a correctness gate. **Higher is better
(maximize).** The initial `solution.py` matches the reference, so the baseline
score is about **1.0x**. A solution that fails the correctness check on any
instance scores **0.0**.

## Layout

| File | Role | Editable? |
| --- | --- | --- |
| `solution.py` | The solver Arbor optimises (`solve(problem)`). | **Yes — this is the only edit surface.** |
| `task.py` | Problem generator, reference solver, `is_solution` verifier. | No — protected ground truth. |
| `eval.py` | Correctness gate + median-of-N timing; prints `score:`. | No — protected harness. |
| `eval.sh` | Pins a single core / single BLAS thread, then runs `eval.py`. | No — protected harness. |

## Dev / test discipline

Dev and test use **disjoint seed ranges** (`1000+` vs `9000+`), so the signal
Arbor iterates on is never the data it is finally judged on:

```bash
bash eval.sh dev     # iterate here
bash eval.sh test    # held-out gate for merges
```

## Tuning runtime

Problem size (the AlgoTune `--target-time-ms` analogue) is set via environment
variables — raise them for a heavier, more discriminating benchmark:

```bash
KNN_N_DB=8000 KNN_N_QUERY=500 KNN_DIM=32 KNN_TRIALS=7 bash eval.sh dev
```

| Var | Meaning | Default |
| --- | --- | --- |
| `KNN_N_DB` | database points per instance | 2000 |
| `KNN_N_QUERY` | query points per instance | 200 |
| `KNN_DIM` | dimensionality | 16 |
| `KNN_INSTANCES` | instances per split | 3 |
| `KNN_TRIALS` | timing repeats (median) | 5 |

## Run it with Arbor

Arbor runs experiments in **git worktrees off the repo root**, so run the example
from a **copy outside the Arbor checkout** — that keeps your Arbor clone's git
history clean and avoids preflight failures from the surrounding repo:

```bash
cp -r examples/algotune_knn /tmp/algotune_knn   # copy out of the Arbor repo
cd /tmp/algotune_knn
git init -q && git add -A && git commit -qm "baseline"  # Arbor wants a clean repo
arbor   # then in the intake chat, confirm the contract below
```

Suggested research contract:

- **Metric**: maximize the `score:` (speedup) printed by `bash eval.sh`.
- **Baseline**: ~1.0x (solution equals reference).
- **Dev/test**: iterate on `bash eval.sh dev`; gate merges on `bash eval.sh test`.
- **Off-limits**: do **not** edit `task.py`, `eval.py`, or `eval.sh`; only
  `solution.py` may change. Output must keep passing `is_solution`.

### What a run looks like

A 6-cycle run with `gpt-5.5` took the dev speedup from **1.01x → 7.77x**
(held-out test **1.00x → 7.22x**), exploring several independent mechanisms in
the idea tree — `argpartition` partial selection, the
`|x-y|² = |x|² − 2x·y + |y|²` GEMM distance expansion, dimension-specialized
accumulation, and database-blocked scanning — and merging the variant that held
up on the held-out split. Your numbers will vary with model and hardware.

## Why this is a good first benchmark

- **Fast & free** — pure NumPy, CPU-only, sub-second, no network.
- **Deterministic** — fixed seeds; speedups aren't polluted by data noise.
- **Real headroom** — multiple independent optimisations (partial selection via
  `argpartition`, the `|x-y|^2 = |x|^2 - 2x·y + |y|^2` GEMM expansion, dtype and
  blocking tweaks) give the hypothesis tree several genuine branches to explore,
  not a single trick.
- **Cheat-proof** — `is_solution` recomputes the ground truth independently, so
  "fast but wrong" can't score.
