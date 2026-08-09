# Autoresearch Architecture Design Deployment

This directory vendors the pinned upstream Autoresearch benchmark source and
adds a project-owned deployment layer. The upstream root files remain unchanged.

## Scope

The installed benchmark is the single-GPU Architecture Design task:

- `prepare.py` and its evaluation constants are fixed;
- an outer research Agent may modify `train.py`;
- each candidate runs for 300 training seconds;
- `val_bpb` is the primary metric and lower is better.

This integration provides source, a locked UV environment, data preparation,
installation verification, a baseline launcher, and the reconstruction
protocol implementation under `BenchmarkAdapters/AutoResearch/`. That adapter
includes a 48-hour outer supervisor, host-owned seed injection, two sealed
held-out evaluations, seven distinct native Agent entries, failure-aware
three-seed aggregation, and a scorecard CLI. Per-Agent model adapters freeze
GPT-5.5/high/temperature 1.0 and record a redacted endpoint identity. No
7×3×48h campaign or new formal score is claimed by
the implementation alone. The complete protocol and honesty boundaries are in
`BenchmarkAdapters/docs/AUTORESEARCH_SEVEN_AGENT_ADAPTER_PLAN.md`.

## Machine Assets

Git contains source and reproducibility metadata only. The following stay under
`autoresearch/.runtime/` or other ignored paths:

- managed Python and `.venv` installations;
- UV, Hugging Face, TorchInductor, Triton, and model caches;
- downloaded parquet shards and generated tokenizer files;
- training logs, candidate worktrees, and results.

Do not commit those assets. The original server deployment remains outside this
repository at `/mnt/sda/shijianwang/benchmark-deployments/`.

## Install

From the integration repository root:

```bash
bash BenchmarkAdapters/environments/install.sh autoresearch
```

Or use the benchmark-local entry point:

```bash
bash autoresearch/scripts/install.sh
```

Both use the tracked `uv.lock` and a UV-managed Python 3.10 runtime. This is
important because TorchInductor needs `Python.h`, which was absent from the
server's original system Python runtime.

Set `UV_PROJECT_ENVIRONMENT`, `AUTORESEARCH_HOME`, `UV_CACHE_DIR`, and
`AUTORESEARCH_RUNTIME_ROOT` to reuse an existing machine installation without
copying it into this Git repository.

## Prepare Data

Minimal smoke data:

```bash
bash autoresearch/scripts/prepare_data.sh --num-shards 1 --download-workers 1
```

Official default preparation uses ten training shards:

```bash
bash autoresearch/scripts/prepare_data.sh
```

Prepared data and tokenizer files are written below
`autoresearch/.runtime/home/.cache/autoresearch/`.

## Verify and Run

```bash
bash autoresearch/scripts/verify_installation.sh
CUDA_VISIBLE_DEVICES=0 bash autoresearch/scripts/run_baseline.sh
```

Set `AUTORESEARCH_REQUIRE_CUDA=1` during verification when a GPU must be
available. `run_baseline.sh` removes SOCKS proxy variables because the locked
HTTPX stack does not include the optional `socksio` dependency; HTTP/HTTPS proxy
variables remain available.

The seven-Agent adapter CLI is available from the repository root:

```bash
BenchmarkAdapters/.venv/bin/python -m BenchmarkAdapters autoresearch \
  --agent ear \
  --protocol autoresearch/protocol/protocol.json \
  --prepared-root /srv/autoresearch-prepared \
  --kernel-cache-root /srv/hf-cache/models--varunneal--flash-attention-3 \
  --environment-python /srv/autoresearch-env/bin/python \
  --output-dir /runs/autoresearch/ear/seed-0 \
  --seed 0 \
  --gpu-id 0 \
  --cpu-set 0-31 \
  --memory-limit-gib 128 \
  --model-config /secure-config/model-track.json \
  --agent-variant g3@FULL_COMMIT
```

Formal execution requires an explicit H100 80GB device, a clean source tree,
prepared assets matching `protocol/prepared_manifest.json`, the locked
FlashAttention cache matching `protocol/kernel_cache_manifest.json`, the locked
Python environment, bubblewrap isolation, the Agent-specific model Adapter, and
a completed `protocol/baseline_score_record.json`. The tracked record remains
`pending` until a clean baseline is run on the final frozen prepared-data scope,
so 48-hour formal launches currently fail closed while smoke/pilot remain usable.
Use `--dry-run` to inspect dispatch and protocol metadata without calling a
model API or running a candidate. Dry-run and synthetic test results are never
formal scores.

Use `--smoke --outer-budget-seconds 1800` for a non-comparable real scored smoke,
or `--pilot --outer-budget-seconds 14400` for a reduced-budget pilot. Both keep
the real 300-second candidate evaluator and sealed two-seed final gate. Install
an Agent runtime reproducibly with, for example,
`bash BenchmarkAdapters/environments/install.sh autoresearch.ear`.
The five Python Agent profiles import archived read-only source snapshots from
their locked environments; Codex and Claude Code remain version-pinned host CLI
probes. Install all seven profiles with:

```bash
for agent in ear mlevolve arbor codex claude-code ml-master-2 ai-scientist; do
  bash BenchmarkAdapters/environments/install.sh "autoresearch.${agent}"
done
```

## Historical Host Smoke

The external deployment was verified on 2026-08-01 with an NVIDIA H100 PCIe,
PyTorch `2.9.1+cu128`, and one training shard. A complete 300-second training
run finished successfully with approximately 45 GB peak VRAM and
`val_bpb=1.111651`. This was a deployment smoke test, not a comparable benchmark
score and not an Arbor-paper reproduction. A credential-free verification
summary is tracked in `config/integration_verification.json`.
