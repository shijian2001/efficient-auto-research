# Docker Evaluation Harness

This directory contains the native MLE Docker runner and historical experiment
launchers. Formal seven-Agent runs enter through `BenchmarkAdapters mle-cell`,
which still calls `run_in_docker.sh` for EAR, MLEvolve and Arbor. Current campaign
commands and paused-run handling live in the
[runbook](../BenchmarkAdapters/docs/CAMPAIGN_LAUNCH.md); this page documents runner
arguments, mounts and artifacts.

## EAR Generation

The canonical EAR source selected by the current adapters is G3:
`mle-bench-agents/efficient-auto-research@7cd9ed5`.
The G5 worktree below is retained for historical infrastructure experiments and
is not a completed seven-Agent benchmark result.

```text
/mnt/sdc/shijianwang/efficient-agent-research/mle-bench-agents/efficient-auto-research
```

The G3 code uses candidate-reported local `METRIC` to drive the search, while
the final outer submission is graded by the official MLE-Bench grader after the
run. G4-G7 remain available as historical experiments and are not the current
canonical adapter source.

## Quick Start

The seven-Agent MLE campaign requires the host HTTP proxy at
`http://127.0.0.1:17892`. All MLE launchers use it for external downloads;
localhost LLM relay requests remain local. The four bwrap-based Agents receive
a per-run Docker-bridge TCP forwarder to this proxy, which also works for
AiScientist's nested containers. Public/private data mounts are unchanged.

Start the dedicated proxied model relay before launching the campaign:

```bash
bash docker-eval/install_crane.sh
bash docker-eval/start_mle_host_relay.sh
```

The relay reads credentials from `~/.mle_relay_env`, listens on `127.0.0.1:6201`,
and sends upstream model requests through port 17892. The existing port-6200
relay can continue serving older runs. The model-track JSON selects port 6201
for newly launched cells, including cells in an already-running campaign.

Missing Docker images are downloaded through 17892 using pinned `crane`, then
imported with `docker load`; `docker run --pull=never` prevents a fallback to an
unproxied daemon download. This does not change or restart the shared Docker
daemon. AiScientist's locally built `aisci-mle:test` image must still exist as
required by its `never` pull policy. The proxy is checked before each new MLE
cell; an unavailable proxy stops that cell instead of falling back to direct
downloads. Existing running Agents keep their original environment.

```bash
cd /mnt/sdc/shijianwang/efficient-agent-research/docker-eval

export OPENAI_API_KEY="<server-provided-key>"
export EAR_AGENT_DIR=/mnt/sdc/shijianwang/efficient-agent-research/mle-bench-agents/efficient-auto-research
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

Use a high step cap plus wall-clock timeout for a time-budgeted historical run.
Do not use a smoke result in a formal table. A formal seven-Agent comparison
must use the frozen protocol, model track, source commits, and scorecard under
`BenchmarkAdapters/`.

## EAR-Specific Environment

| Variable | Meaning |
|---|---|
| `EAR_AGENT_DIR` | clean committed Agent checkout; current formal EAR uses G3 |
| `RUN_TAG` | unique run ID and output namespace |
| `SEED` | Python/NumPy controller seed |
| `EAR_INITIAL_ROOT_ATTEMPTS` | historical generation-specific root policy; used only if the selected CLI supports it |
| `EAR_NEW_ROOT_STAGNATION` | historical generation-specific stagnation policy |
| `EAR_NEW_ROOT_COOLDOWN_ATTEMPTS` | historical generation-specific root cooldown |
| `MODEL` | LLM model rewritten by relay; default `gpt-5.5` |
| `LLM_REASONING_EFFORT` | relay reasoning effort; default `high` |
| `DETACH=1` | unsupported; runner exits because relay/format services share its lifecycle |

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

The runner launches `BenchmarkAdapters/LLMRelay/server.py` on an allocated host
port and exposes it through the Docker bridge. Port leases live in
`.runtime/port-locks/`; relay, grading and download-forwarder ports are recorded
for the run. They are not a fixed `6200 + GPU_ID` / `5200 + GPU_ID` table.

The shared model-track currently points to the host service on 6201. Dedicated
controllers can supply a different model-track, such as v7's 6202/6203 lanes.
Protocol conversion, output caps and token-accounting semantics are documented
once in the [relay README](../BenchmarkAdapters/LLMRelay/README.md).

Runner token logs retain the per-run path:

```text
run-logs/<RUN_TAG>_token_usage/<agent>_<task>_gpu<N>.jsonl
```

Upstream credentials are passed through environment variables, not the manifest.
Use the [campaign runbook](../BenchmarkAdapters/docs/CAMPAIGN_LAUNCH.md) for shared
service startup rather than copying a historical port configuration.

## Hardware and Mounts

- One host GPU device is mapped as container `nvidia0`; candidate sees
  `CUDA_VISIBLE_DEVICES=0`.
- Each container receives 21 CPU cores by default, offset by `GPU_ID`.
- Shared memory is 8 GiB.
- The host conda environment is mounted read-only.
- Project public data/cache paths are mounted for the outer agent and candidate.
- Relay, grading and download proxy ports are allocated with per-port locks; read the run records for actual values.

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

## Historical launcher names

`launch_12h_ear_latest_6task.sh` and `watch_clean_gpus_and_launch_g7.sh` default to
`ear-worktrees/g7-converged`, but that worktree was observed at G3 `7cd9ed5` on
branch `ear/g3-rollback` on 2026-09-20. The directory/script name is not a version
identifier. Historical launchers are not the current campaign resume interface;
check the actual commit and generation before using one for a separate experiment.
