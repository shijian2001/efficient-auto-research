# modded-NanoGPT Optimizer Design

This directory freezes the Benchmark-owned runtime and protocol assets for the
modded-NanoGPT Track 3 Optimizer Design reconstruction.

## Frozen Identity

- Upstream: `https://github.com/KellerJordan/modded-nanogpt.git`
- Commit: `bc1b58e83fa499c5df268bd6c8b98701273b96e7`
- Track 3 tree: `05bdf00394b7dee564500e9a6fdb472ce67a1659`
- Editable artifact: `records/track_3_optimization/train_gpt_simple.py`
- Protocol: `modded-nanogpt-optimizer-design-reconstruction-v1`
- Outer model: GPT-5.5, reasoning effort `high`, temperature `1.0`
- Formal budget: three 48-hour outer seeds per Agent
- Candidate hardware: four exclusive H100 80GB GPUs

This is a public-source reconstruction. It is not directly comparable with
Arbor's reported private-workspace 4xA100 result.

## Adapter Layers

The reusable Benchmark layer lives in `BenchmarkAdapters/OptimizerDesign/`.
It owns source/data/runtime identity, policy validation, resource leases,
sandboxed execution, dev and held-out evaluation, immutable records,
aggregation, and scorecard validity.

The seven small Agent adapters live in
`BenchmarkAdapters/OptimizerDesign/agents/`. They only select the Agent's
native search component and bind the shared optimization task environment.
They do not implement scoring, evaluation, resource, or protocol policy.

## Current Gate

The implementation and all seven dry-run contracts are command-ready. Formal
runs remain fail-closed because `protocol/baseline_score_record.json` is still
`pending`. Generate the protected two-seed baseline on an idle four-H100 host,
promote its evidence, regenerate `protocol.json`, and commit a clean adapter
tree before starting the 21 formal Agent-by-seed cells.

```bash
BenchmarkAdapters/.venv/bin/python -m BenchmarkAdapters \
  optimizer-design-protocol \
  --validate optimizer-design/protocol/protocol.json

BenchmarkAdapters/.venv/bin/python -m BenchmarkAdapters \
  optimizer-design --agent ear \
  --protocol optimizer-design/protocol/protocol.json \
  --output-dir /tmp/optimizer-design-ear-dry --seed 0 --dry-run
```

See `BenchmarkAdapters/docs/OPTIMIZER_DESIGN_SEVEN_AGENT_ADAPTER.md` for the
baseline promotion and formal campaign procedure.
