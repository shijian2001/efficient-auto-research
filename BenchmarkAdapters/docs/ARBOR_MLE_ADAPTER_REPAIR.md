# Arbor MLE-Bench Adapter Repair

## Status

The narrow Adapter repair is implemented in the Arbor MLE integration. It still
requires a short end-to-end smoke and does not constitute a completed formal
campaign.

## Scope

The Adapter owns artifact identity and the boundary to the host-owned private
grader. Arbor owns its research behavior.

The repair MUST NOT change:

- Arbor search, validation split, candidate code, prompts, retry policy, stop policy;
- Idea Tree, Git branch/worktree, merge, promotion, selection, or recovery policy;
- the representation or interpretation of Arbor's local metric.

Arbor may use any local score representation (`100`, `1.0`, or another value),
rerun validation, choose a poor candidate, or change its internal TEST metadata.
Those are Arbor outcomes and remain local-only evidence.

## Root Cause

The current `mle_eval_state.json` is written below the Agent workspace:

```text
<worktree>/results/mle_eval_state.json
```

Executor worktrees and merge-evaluation worktrees do not share that file. A
later `eval.sh verify` therefore fails mechanically even when the same
`solution.py` and `submission.csv` are present.

## Host-Owned State

Create a host-owned state root outside the Arbor repository and mount it read-only
into every worktree that needs verification. A run may contain many parallel
Executors. The canonical identity is:

```text
run_id / competition_id / node_id / attempt_id
```

`node_id` is Arbor's Executor/Idea Tree identity. Independent top-level Agents
must use independent runs; parallel Executors in one Arbor run share the run
protocol but never share writable artifacts.

Each immutable candidate record contains only binding and audit data:

```json
{
  "schema_version": 1,
  "run_id": "...",
  "competition_id": "...",
  "node_id": "...",
  "attempt_id": "...",
  "solution_sha256": "...",
  "submission_sha256": "...",
  "raw_metric": "100",
  "metric_direction": "maximize",
  "evaluation_role": "local_only",
  "official_grading": false,
  "source_node_id": "..."
}
```

`raw_metric` is retained for audit only. There is no normalized metric, metric
scale, cross-candidate ranking, or Adapter-defined comparison function.

Records are created atomically and never overwritten. A repeated identity or a
hash mismatch is an error. `source_node_id` is provenance, not an authority for
trust.

## Run and Verify

`eval.sh run` executes the candidate using Arbor's existing behavior. After a
successful run, the host-side Adapter finalizer recomputes both file hashes and
commits the immutable record. The Agent cannot directly write the canonical
state. A small host helper or equivalent lifecycle callback may be used; the
transport is an implementation detail.

`eval.sh verify` MUST:

1. resolve the current `run_id/node_id/attempt_id` record;
2. recompute the current `solution.py` and `submission.csv` hashes;
3. compare hashes, competition identity, and record status;
4. return the recorded local metric for diagnostics only.

It MUST NOT execute candidate code, rerun validation, access private labels, or
write state. Failure preserves the original error. The Adapter MUST NOT rewrite
`verify` as `run` or relabel it as `independent_test`.

Arbor itself may still choose to call `run` after a verify failure. That choice
is not intercepted or repaired by the Adapter.

## Final Artifact and Official Grading

Arbor selects its final node/trunk/submission using its native process. The host
does not rank candidates, select a best snapshot, normalize scores, or recover a
candidate by local metric.

The host only accepts the artifact Arbor declares as final, recomputes its hash,
and freezes a copy under a run-owned final directory. The private MLE-Bench
grader then scores exactly that frozen file.

The official grading record MUST bind:

```text
run_id
competition_id
agent/variant identity
final submission_sha256
grader/data version
official score and validity
```

Any hash mismatch, missing grader record, or invalid submission makes the
official result invalid. Arbor `DEV`, `TEST`, `B_test`, local `METRIC`, reports,
and snapshots MUST NOT be used as official scores.

## Required Tests

- `run_id/node_id/attempt_id` records remain distinct for parallel Executors.
- A verify from another worktree succeeds with the same bound files.
- Changed solution, changed submission, symlink, stale record, and record
  overwrite are rejected.
- Verify does not execute code or call a scoring service.
- Arbitrary local score representations are preserved verbatim.
- Local-only evidence cannot enter official aggregation.
- Replacing the frozen final submission invalidates the private grader record.
- A private grader result with a different submission hash is rejected.
- Two to four parallel Executors complete without state or artifact collisions.

No long experiment is required for this repair. A short end-to-end smoke must
cover run, cross-worktree verify, final artifact freezing, and private-grader
hash binding.

## Explicit Non-Goals

This repair does not address Chaii validation overfitting, Arbor algorithm
quality, Git strategy, metric semantics, model selection, relay tuning, missing
formal benchmark assets, or the broader seven-Agent readiness program.

## Source Provenance and the Inner Arbor Repository

`docker-eval/run_in_docker.sh` launches Arbor from `baselines/Arbor-longrun-patched`,
which is itself a git repository nested inside this benchmark repository. The
launcher freezes the Agent by `git archive HEAD` over that **inner** repo and
refuses to start when its subtree is dirty (`SUBTREE=.`, `exit 2`). Two
consequences follow, and both have bitten this adapter:

- An edit committed only to the outer benchmark repository never reaches the
  container. It also leaves the inner subtree dirty, so the clean gate rejects
  every Arbor launch. The `executor_timeout: 14400 -> 7200` change (4h -> 2h per
  executor; under a 12h budget three executor timeouts consumed the whole budget
  and produced zero submissions) was in exactly this state and is now committed
  to `baselines/Arbor-longrun-patched` itself.
- `ARBOR_SOURCE_ALLOW_DIRTY` was assigned in the launcher but read nowhere. It
  advertised an opt-out that did not exist; the clean gate is unconditional. The
  dead assignment has been removed rather than wired up — a dirty-source escape
  hatch would defeat the provenance guarantee this section exists to protect.

**Known provenance mismatch (unresolved).** `MLEBenchLite/campaign.py:199`
records `AGENTS["arbor"].install_path`, which is `baselines/Arbor`, while the
launcher actually executes `baselines/Arbor-longrun-patched`. The manifest's
`agent_commit` and `source_dirty` therefore describe a different working tree
than the code that ran. Fixing it means giving the `arbor` /
`arbor-benchmark-patched` pair a `runtime_path` (the `AgentSpec` field already
exists and `execution_path` already prefers it) so that manifest identity
follows the executed source. That change lives in `registry.py` and is out of
scope here.
