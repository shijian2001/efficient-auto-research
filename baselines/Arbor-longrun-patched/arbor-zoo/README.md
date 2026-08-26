# arbor-zoo

A curated, verifiable set of benchmarks in one standard format — Arbor's own
regression harness first, an outward showcase second. **Quality-capped, not a
leaderboard.** An unverified benchmark does not enter the zoo.

Full format spec and the verifier's check list live in the docs:
**[Benchmark Zoo](../docs/zoo.md)**.

## Format in one line

Each `arbor-zoo/<name>/` folder is one benchmark: a **README.md** (a plain-language
description Arbor reads — the task, the metric, what it may edit, how dev/test differ), a
**PROVENANCE.md** card for humans (source, setup, how the baseline works, contamination,
caveats), a runnable **baseline** (one or more code files), and a protected **eval
entrypoint** (`eval.sh` or `eval.py`) that prints one `score: <float>` line for `dev` and
`test`. The format is documentation-first — there is no machine manifest.

## Packs

| Pack | Domain | Metric | Baseline | Setup | Status |
| --- | --- | --- | --- | --- | --- |
| [`algotune_knn`](algotune_knn/) | algorithm / efficiency | speedup (maximize) | ~1.0x | CPU, offline | ✅ verified |

Folders beginning with `_` (e.g. [`_template`](_template/)) are scaffolding and are
skipped by the tooling.

## Quick commands

```bash
arbor benchmark list arbor-zoo                  # index the benchmarks
arbor benchmark verify arbor-zoo/algotune_knn   # gate one (exits non-zero on failure)
```

To run Arbor on a benchmark, copy it **out** of this checkout first (Arbor uses git
worktrees off the repo root):

```bash
cp -r arbor-zoo/algotune_knn /tmp/algotune_knn
cd /tmp/algotune_knn && git init -q && git add -A && git commit -qm baseline
arbor
```

## Add a benchmark

Copy `_template`, fill it in, and verify it green — see
[docs/zoo.md → Add a benchmark](../docs/zoo.md). Drafting may be automated; acceptance
is a human step, and the baseline-implementing agent must be separate from the loop that
later optimizes it.
