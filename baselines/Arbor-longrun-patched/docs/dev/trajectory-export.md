# Trajectory Export (self-evolution · line 1)

Status: spec for review. No code yet.

## Goal

Every finished run already writes `events.jsonl`. This adds a clean,
training-ready dump so users can collect runs for SFT/RL. No new top-level
command — it rides the existing finalize step, like `REPORT.md` does.

## What the user sees

After a run, the session dir gains one file: `trajectory.jsonl`. That's it. A
config flag turns it off:

```yaml
evolution:
  export_trajectory: true   # default on; writes trajectory.jsonl at finalize
```

No new verb, no extra prompt. Collecting many runs = globbing
`.arbor/sessions/*/trajectory.jsonl`.

## What it is — two granularities

Prior art: NVIDIA Polar (arXiv 2605.24220, "Agentic RL on Any Harness at
Scale"). Polar proxies the LLM API boundary, captures token-faithful traces
(messages, token_ids, logprobs, loss mask, reward) and feeds async RL trainers.
Lesson: for RL you need token-level fidelity, not coarse decision logs. Arbor
needs **no proxy** — it owns its provider layer, so it records the same thing
directly at the call site.

- **Coarse trace** (`trajectory.jsonl`): one line per decision point — for
  analysis and SFT of the coordinator policy. Cheap, always on.
- **Token-faithful trace** (`tokens.jsonl`, opt-in): one record per LLM call —
  messages, sampled token_ids, logprobs, loss mask (only behavior-policy tokens
  trainable), reward back-filled from eval. RL-ready, Polar-compatible. Needs a
  provider that returns logprobs/token_ids (OpenAI yes; Anthropic limited — note
  the gap, don't fake it).

## Where the data comes from

All of it already exists; this just joins it up. Source = `events.jsonl` +
`.coordinator/idea_tree.json`:

| Record field | Source event |
| --- | --- |
| `cycle`, `phase` | `cycle.start` / `cycle.phase` |
| `proposed` (node_id, hypothesis, parent) | `idea.proposed` |
| `executor` action (branch, code edits) | `executor.start` / `executor.end` |
| `reward` (dev score, delta) | `eval.end` / `idea.completed` |
| `decision` (merge/prune/stop) | `idea.merged` / `idea.pruned` |
| outcome status, final insight | idea_tree node |

## Record shape (one line)

```json
{
  "run": "run_20260628_230556",
  "step": 4,
  "cycle": 2,
  "node_id": "2.1",
  "parent_id": "2",
  "state": {"frontier": [...], "best_score": 2.16, "constraints": "..."},
  "action": {"kind": "ideate", "hypothesis": "GEMM-expanded squared distances + argpartition"},
  "reward": {"dev_score": 7.50, "delta": 5.34, "merged": true},
  "tokens": 16512
}
```

State is the tree digest the coordinator was grounded on (we already build this
for IDEATE — reuse it). Reward fills in when that node's `eval.end` lands;
non-scored steps get `reward: null`.

## How it's built

1. Reuse `export.py`'s session resolver + jsonl reader (don't rebuild).
2. Replay `events.jsonl` in order, carry a small state, emit one record per
   decision point, back-fill reward when eval ends.
3. Write `trajectory.jsonl`. Hook = same finalize path that writes `run_stats.json`.
4. `arbor export <session> traj.jsonl` keeps working for old runs (offline).

## Validate (no API needed)

Run on the existing `/tmp/algotune_knn_dogfood` session: confirm ~6 ideas →
records, scores back-filled, file loads in a training loop. Pure transform, no
model calls — cheap, deterministic.

## Out of scope (line 2)

Distilled skills, preferences, novelty ledger, recall. This file is only the raw
training dump. Skills sit next to it later, same finalize hook.
