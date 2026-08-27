# Shared Baseline Adapters

The canonical implementation and capability matrix are documented in
`BenchmarkAdapters/README.md`.

## Current State

The adapter code, native launcher definitions, host-owned graders, and scorecard
logic are present. This does not mean that all fourteen Agent x Benchmark cells
can currently produce formal scores. Current checkout status:

- MLE-Bench Lite `data_manifest.json` is schema 2 (digest
  `6b9bcf44f6f28c965d3da3eb76cfaf24ebb61c384ace4cd82a4a354ffc1db04a`), frozen
  from this host's `mle-bench-data/` prepared trees.
- Terminal AO protocol is schema 2 as of 2026-08-27: `benchmark_source_commit`
  `2fd12b88aafdd04a52c298e3940bcb189f9766d6`, `inner_model`
  `configured-by-model-track`, `seeds = (0,)`, digest
  `0c4444b0f19d3e145d7049c85b627a876ce4a546b0534afdded1cc1ab263031e`.
  `require_formal_contract()` passes. How that commit was established (indirect
  evidence, not a local git read): `ON_DISK_AGENT_VERSIONS.md`.
- This campaign reports **N=1** on both benchmarks. `repetition_summary` then
  emits `reporting_label: single_run` with `mean` only: standard deviation,
  standard error and the 95% CI are all `null`. Scorecards therefore rank by a
  single observation per cell and cannot separate a real gap from run-to-run
  noise; do not describe an N=1 ordering as a significant difference.
  Frozen protocols: `configs/mle-protocol.n1-12h.json` (22 tasks, seed 0,
  12h wall clock) and `terminal-bench-2/ao_protocol/protocol.json`
  (36/53 split, seed 0, 48h outer).
- Shared model-track for this host: `configs/model-track.gpt-5.6-terra-host-relay.json`
  (gpt-5.6-terra, temperature 1.0, reasoning_effort high, relay
  `http://127.0.0.1:6200/v1`) — the track for the upcoming campaign. The earlier
  `model-track.gpt-5.5-host-relay.json` is kept unchanged so already-recorded
  gpt-5.5 runs stay reproducible; do not mix the two in one comparison. The
  placeholder file remains a template only.
- `BenchmarkAdapters/.venv` includes PyYAML.
- Nested Agent checkouts are still dirty; formal preflight will refuse those
  cells until each `install_path` is a clean pinned commit. Do not reset
  Arbor / AiScientist / EvoMaster working trees and do not pull upstream.
  This campaign freezes the 2026-08-27 on-disk checkouts (including uncommitted
  patches). Identities: `ON_DISK_AGENT_VERSIONS.md`.

Arbor MLE uses `arbor-benchmark-patched`. Terminal AO uses
`ai-scientist-terminal-variant` for AiScientist; MLEvolve and ML-Master 2.0
are excluded from AO (task-shape mismatch). The 89-task direct Terminal
path is a smoke path and its scores must not be compared with Terminal AO.

How to start the later formal campaign — comparison set, freeze-assets,
model-track, preflight, per-cell smoke, then 22×7×3 MLE and 5×3×48h AO —
is §17 of `SEVEN_AGENT_BENCHMARK_REPAIR_PLAN.md`. Do not launch the long
runs until those gates pass.

The implemented repair checklist, scoring protocol, remaining real-run gates,
and evidence requirements for all seven Agents on MLE-Bench Lite and
Terminal-Bench 36/53 AO are defined in
`BenchmarkAdapters/docs/SEVEN_AGENT_BENCHMARK_REPAIR_PLAN.md`.

The target protocol, native-Agent adaptation plan, score validity rules,
held-out evaluation design, and Definition of Done for all seven Agents on
Autoresearch Architecture Design are defined in
`BenchmarkAdapters/docs/AUTORESEARCH_SEVEN_AGENT_ADAPTER_PLAN.md`. The protocol,
shared Benchmark adapter, seven native Agent bridges, held-out evaluator, and
aggregation commands are implemented; real scored campaigns remain separate
readiness gates.

The two-layer modded-NanoGPT Track 3 Optimizer Design implementation, frozen
upstream identity, baseline promotion procedure, and formal comparison rules are
documented in
`BenchmarkAdapters/docs/OPTIMIZER_DESIGN_SEVEN_AGENT_ADAPTER.md`.

Per-cell adapter documentation — how each Agent is wired into each benchmark,
one file per Agent x Benchmark cell — is in `BenchmarkAdapters/docs/adapters/`
(index: `docs/adapters/README.md`).

How to actually launch this campaign — the host relay on port 6200 that must be
started first, the frozen protocol/model-track paths, the per-cell
`--agent-variant` values, and known traps — is `CAMPAIGN_LAUNCH.md`.

The important boundary is benchmark-specific:

- `BenchmarkAdapters/MLEBenchLite/adapter.py` builds MLE-Bench Lite workspaces
  and native launch commands.
- `BenchmarkAdapters/TerminalAO/supervisor.py` owns 36-dev search, revision
  selection, final freeze, and one-shot 53-test evaluation.
- `BenchmarkAdapters/TerminalBench/adapter.py` is a direct-solving smoke path
  and is not comparable with Terminal AO.
- `BenchmarkAdapters/AutoResearch/` provides reusable autonomous optimization
  launchers; `BenchmarkAdapters/OptimizerDesign/` specializes that architecture
  with Benchmark-owned optimizer policy and scoring.
- `BenchmarkAdapters/FMLBench/` is the first-class pinned-upstream FML formal
  shared benchmark layer plus seven concrete native Agent adapters.
  `FML_SEVEN_AGENT_ADAPTER.md` documents protocol review, evidence replay,
  readiness, and the remaining deployment gates. `BenchmarkAdapters/FLM-bench/`
  is a retained non-formal smoke compatibility path only.
- `terminal-bench-2/agent_adapters/shared/harbor_shell.py` is the common
  synchronous Harbor environment bridge used by native host-side Agent loops.

Terminal AO never imports an Agent-controlled evaluator. Harbor remains
host-owned; outer Agent processes receive only an aggregate dev capability and
cannot see the test split or test endpoint.

```bash
# Run these only after the adapter environment and model/protocol files have
# been prepared. A dry-run only checks command construction; it is not a score.
BenchmarkAdapters/.venv/bin/python -m BenchmarkAdapters status
BenchmarkAdapters/.venv/bin/python -m BenchmarkAdapters preflight
BenchmarkAdapters/.venv/bin/python -m BenchmarkAdapters terminal-ao \
  --agent codex --protocol terminal-bench-2/ao_protocol/protocol.json \
  --output-dir /runs/ao/codex/seed-0 --seed 0 --dry-run
```

Agent-specific implementation notes are stored beside each checkout in
`adapter_docs/mle-bench-lite.md` and `adapter_docs/terminal-bench.md`.
