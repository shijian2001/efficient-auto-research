# Shared Baseline Adapters

The canonical implementation and capability matrix are documented in
`BenchmarkAdapters/README.md`.

## Current State

The adapter code, native launcher definitions, host-owned graders, and scorecard
logic are present. This does not mean that all fourteen Agent x Benchmark cells
can currently produce formal scores. Current checkout status:

- MLE-Bench Lite schema-v2 data manifest: generate with `mle-freeze-assets`
  against `mle-bench-data/` (22 prepared trees are present on this host).
- Terminal AO protocol is still schema 1 until a 40-char
  `benchmark_source_commit` is supplied to `terminal-ao-protocol`.
- Shared model-track for this host: `configs/model-track.gpt-5.5-host-relay.json`
  (gpt-5.5, temperature 1.0, reasoning_effort high, relay
  `http://127.0.0.1:6200/v1`). The placeholder file remains a template only.
- `BenchmarkAdapters/.venv` includes PyYAML.
- Nested Agent checkouts are still dirty; formal preflight will refuse those
  cells until each `install_path` is a clean pinned commit. Do not reset
  Arbor / AiScientist / EvoMaster working trees without an explicit pin.

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
