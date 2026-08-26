# TODO_pack_name

One-line summary of the benchmark — this first line shows up in `arbor benchmark list`.

This README is what **Arbor reads** at intake to understand the task. Write it in plain
language; there is no rigid format. Cover the four things below.

## The task

What the task is and what a solution looks like.

## Metric

What `eval.sh` / `eval.py` prints (one `score:` line) and whether **higher or lower** is
better.

## What Arbor may edit

Which file(s) are the editable baseline (e.g. `solution.py`), and what's off-limits (the
eval harness and any ground-truth / data).

## Dev / test

How dev and test differ, so the held-out split is clear — disjoint seeds, or separate
`data/dev/` and `data/test/` folders.

## Run it

```bash
python eval.py --split dev    # iterate here
python eval.py --split test   # held-out gate
```

See [`PROVENANCE.md`](PROVENANCE.md) for source, setup, and the baseline write-up.
