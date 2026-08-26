# Self-Evolution: learning from past runs

Arbor remembers what it learns. Every run leaves **concrete, situational findings**
— a dataset quirk that lifted the metric, a trap an executor or the harness fell into
— and a later run on a similar task can start from that experience instead of from
scratch.

There are two halves, and you only ever touch the second one.

## Capture (automatic)

While a run is in progress, the Coordinator can log a finding the moment it hits one,
via the **`RecordFinding`** tool — for example:

- *leverage* — "the dataset's labels above index 9000 are noisy; dropping them adds ~2 points"
- *pitfall* — "the executor kept editing `eval.py`; remind it the harness is protected"

At the end of the run, Arbor consolidates these (plus, optionally, findings it mines
from the run itself) into an **`EXPERIENCE.md`** inside that run's session folder:

```
.arbor/sessions/<run_name>/
  EXPERIENCE.md      # the run's concrete findings (leverage / pitfall)
  findings.jsonl     # raw findings logged live during the run
  trajectory.jsonl   # decision trace (for SFT/RL)
```

This is **on by default** — it only writes a notes file, costs nothing extra, and is
what makes the next run smarter. To turn it off:

```yaml title="research_config.yaml"
coordinator:
  distill_skills: false        # don't write EXPERIENCE.md
  distill_abstract: false      # (opt-in) also LLM-mine the run for extra findings
```

`distill_abstract` is the only part that spends extra LLM calls — it asks the model to
surface concrete findings the agent never logged explicitly. It's **off by default**;
turn it on when you want richer experience and don't mind a couple of calls at finalize.

## Reuse (you decide, in the intake conversation)

The next time you start a run in the same project, the **intake conversation** checks
whether earlier runs left experience. If your new goal is similar, the planning agent
offers it and asks whether to reuse it — you stay in control:

```
You: optimize the kNN solver for speed
Arbor: I found experience from a past run on this project
       (a GEMM + argpartition win, a cKDTree dead-end, a macOS taskset gotcha).
       Want me to start from those findings? [yes]
You: yes
```

When you agree, the relevant findings are composed into a short **priors** block and
prepended to the instruction the Coordinator runs on — so it begins already knowing the
dead-ends to skip and the directions that worked. Findings seen across several past runs
are tagged `[xN]` and ranked first, so repeated lessons carry more weight.

Nothing is forced: the priors are presented as *candidate* directions to verify, not
rules. Arbor's normal dev/test discipline still filters them — a stale or wrong prior
just becomes a quickly-pruned branch, never a silent corruption of the result.

## What experience is (and isn't)

Findings are kept **specific on purpose**. The value of "at d=16 spatial trees collapse;
fuse distance + selection" is the detail — it's what lets the next run skip the same
exploration. Arbor does **not** try to abstract findings into generic principles
("prefer partial selection over full sort"), which read well but rarely help.

Experience lives **per session**, not in a global library, so it never bloats the curated
[Skills](skills.md) menu. Recall is scoped to the current project, so an unrelated task
never inherits another domain's tricks.

## In short

| | Who triggers it | Default |
| --- | --- | --- |
| **Capture** (`EXPERIENCE.md`) | automatic, at finalize | on |
| **LLM mining** (`distill_abstract`) | config flag | off |
| **Reuse** at intake | you, in conversation | offered when a match exists |
