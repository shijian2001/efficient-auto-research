# Docker Evaluation Harness

This directory launches one agent per container with a fixed GPU/CPU allocation
and an in-container LLM relay. It supports EAR, MLEvolve, and Arbor. The host
harness is outside the EAR Git worktree, so every formal run records launcher and
relay SHA-256 values in `launch_manifest.json`.

## EAR Generation

The current EAR implementation candidate is `ear/g7`, developed in:

```text
/mnt/sdc/shijianwang/efficient-agent-research/ear-worktrees/g7-converged
```

G7 follows the G3 reward path: candidate-reported local `METRIC` drives the
search, while the final outer submission is graded by the official MLE-bench
grader after the run. G6 remains available as a separate historical controlled-
evaluator experiment and is not part of this launcher path.

## Quick Start

```bash
cd /mnt/sdc/shijianwang/efficient-agent-research/docker-eval

export OPENAI_API_KEY="<server-provided-key>"
export EAR_AGENT_DIR=/mnt/sdc/shijianwang/efficient-agent-research/ear-worktrees/g7-converged
export RUN_TAG=<unique-run-id>
export SEED=0

bash run_in_docker.sh efficient-auto-research <task-id> <gpu-id> <steps> <timeout-seconds>
```

Launcher arguments:

| Position | Value | Default |
|---|---|---|
| 1 | `efficient-auto-research`, `MLEvolve`, or `Arbor` | required |
| 2 | task/competition ID used by the outer harness | required |
| 3 | host GPU ID | `0` |
| 4 | maximum steps | `2` |
| 5 | wall-clock timeout in seconds | `900` |

Use a high step cap plus wall-clock timeout for a time-budgeted run. Do not use a
smoke result in a formal table.

## EAR-Specific Environment

| Variable | Meaning |
|---|---|
| `EAR_AGENT_DIR` | clean committed G7 worktree |
| `RUN_TAG` | unique run ID and output namespace |
| `SEED` | Python/NumPy controller seed |
| `EAR_INITIAL_ROOT_ATTEMPTS` | independent roots during bootstrap; default `3` |
| `EAR_NEW_ROOT_STAGNATION` | stagnation threshold; default `8` |
| `EAR_NEW_ROOT_COOLDOWN_ATTEMPTS` | minimum gap between fresh roots; default `4` |
| `MODEL` | LLM model rewritten by relay; default `gpt-5.5` |
| `LLM_REASONING_EFFORT` | relay reasoning effort; default `high` |
| `DETACH=1` | run container in background |

Candidate code runs in an attempt-local subprocess with the public data directory
and its own current working directory. The launcher does not inject a hidden
evaluator or task-specific adapter. Official private answers remain outside the
search process and are used only by the post-run MLE-bench grader.

## Launch Manifest

EAR writes a credential-free `launch_manifest.json` before creating the
container. Schema v2 records:

- task identifier, run ID, seed, model, temperature, reasoning effort;
- steps, wall-clock timeout, GPU, CPU set/count, and data path;
- agent commit, branch, and dirty state injected by the host;
- launcher/relay hashes and container image;
- search reward semantics (`candidate_reported_metric`), root-policy parameters,
  and the outer post-run grading mode.

The agent report records the manifest SHA-256 separately from its canonical
config SHA-256.

## LLM Relay

Each container starts `llm_relay_proxy.py` on host-network port `6200+GPU_ID`:

```text
agent -> http://127.0.0.1:620X/v1 -> relay proxy -> configured upstream
```

The proxy rewrites the model, injects reasoning effort, normalizes unsupported
parameters/messages, retries transport/rate/server failures, handles streaming
and tool-call compatibility, and writes token usage to:

```text
run-logs/<RUN_TAG>_token_usage/<agent>_<task>_gpu<N>.jsonl
```

Relevant variables are `UPSTREAM_BASE_URL`, `UPSTREAM_API_KEY` or
`OPENAI_API_KEY`, `LLM_UPSTREAM_TIMEOUT`, and `LLM_MAX_RETRIES`. Credentials are
forwarded only through environment variables and are never printed or placed in
the manifest.

## Hardware and Mounts

- One host GPU device is mapped as container `nvidia0`; candidate sees
  `CUDA_VISIBLE_DEVICES=0`.
- Each container receives 21 CPU cores by default, offset by `GPU_ID`.
- Shared memory is 8 GiB.
- The host conda environment is mounted read-only.
- Project public data/cache paths are mounted for the outer agent and candidate.
- LLM proxy ports use `6200+GPU_ID`; baseline grading ports use `5200+GPU_ID`.

## Outputs

| Agent | Primary output root |
|---|---|
| EAR | `$EAR_AGENT_DIR/docker_runs/<RUN_TAG>_<task>/` |
| MLEvolve | `baselines/MLEvolve/runs/<timestamp>_<task>/` |
| Arbor | `run-logs/<RUN_TAG>_Arbor_<task>_gpu<N>/` |

EAR layout:

```text
docker_runs/<RUN_TAG>_<task>/
├── launch_manifest.json
├── <published artifact>
└── workspace/runs/<run_id>/
    ├── report.json
    ├── attempts/
    ├── artifacts/
    ├── traces/
```

An outer output is removed before any fallible agent startup work and published
only from the current report's verified final hash. Timeout, nonzero exit,
missing report, degraded finalization, or hash mismatch cannot be graded merely
because a stale file exists.

## Formal-Run Checklist

1. Source worktree is clean and full commit is recorded.
2. Full unit suite and launcher `bash -n` pass.
3. A separately authorized minimal real smoke has passed in this exact container
   setup.
4. Manifest contains no credential and matches report identities.
5. Final artifact hash closes from controller artifact to report to outer file.
6. Official scoring occurs only after the declared run ends.

## Baseline Notes

MLEvolve follows its published launcher defaults with cold-start explicitly
disabled. Baseline-specific logic remains in this outer comparison harness and
never enters EAR `agent/`.

Comparisons must use complete runs and the same official scoring convention. Do
not splice task scores or artifacts across EAR generations/runs.

## Troubleshooting

`NO GPU`: verify the selected `/dev/nvidia<N>` and host `nvidia-smi`.

Relay failure: inspect `/tmp/llm_proxy_<gpu>.log` inside the running container.

Model download failure: verify the host proxy and writable model cache.

## Credential Rotation

The launcher has no hardcoded API credential. Any previously exposed credential
must remain revoked and be rotated server-side. New credentials are accepted only
through `UPSTREAM_API_KEY` or `OPENAI_API_KEY`.
