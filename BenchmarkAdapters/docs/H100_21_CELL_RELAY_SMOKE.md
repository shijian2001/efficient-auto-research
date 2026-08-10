# H100 7-Agent × 3-Benchmark Minimal Relay Smoke

## Scope

This report records a minimal native-launcher transport smoke on the H100 host for:

- AutoResearch Architecture;
- Optimizer Design;
- FML-Bench;
- EAR, MLEvolve, Arbor, Codex, Claude Code, ML-Master 2.0, and AiScientist/AweAI.

Every selected cell used the repository-owned `BenchmarkAdapters/LLMRelay`, which forced the
upstream model to `gpt-5.5`. The maximum API concurrency was one. The host credential was passed
only in process environment and is not stored in this report or the archived run evidence.

This was not a benchmark run. It used a synthetic development capability, one native search step,
and a 60-second process limit. It did not execute GPU training, benchmark evaluators, held-out
evaluation, aggregate scoring, or any formal/comparable score.

## Result Matrix

| Benchmark | EAR | MLEvolve | Arbor | Codex | Claude Code | ML-Master 2.0 | AiScientist |
|---|---|---|---|---|---|---|---|
| AutoResearch Architecture | clean exit | clean exit | clean exit | clean exit | clean exit | Relay passed; 60s limit | clean exit |
| Optimizer Design | clean exit | clean exit | clean exit | clean exit | clean exit | clean exit | clean exit |
| FML-Bench | clean exit | clean exit | clean exit | clean exit | clean exit | Relay passed; 60s limit | clean exit |

Summary:

- Relay transport and forced-model evidence: 21/21 cells;
- native clean exit within 60 seconds: 19/21 cells;
- ML-Master AutoResearch and FML produced valid Relay calls before reaching the smoke time limit;
- selected cell traffic: 43 successful upstream requests and approximately 104,919 tokens;
- total traffic including six Relay readiness probes: approximately 104,997 tokens;
- API concurrency: one.

## Per-Benchmark Telemetry

| Benchmark | Relay cells | Clean exits | Requests | Approx. tokens |
|---|---:|---:|---:|---:|
| AutoResearch Architecture | 7/7 | 6/7 | 13 | 27,813 |
| Optimizer Design | 7/7 | 7/7 | 15 | 28,260 |
| FML-Bench | 7/7 | 6/7 | 15 | 48,846 |

## Explicit Variants

The smoke did not silently promote patched systems to original Agent identities:

- EAR used the explicit `g3` identity;
- Arbor used `arbor-benchmark-patched` where the reviewed original runtime was unavailable;
- ML-Master used `ml-master-autoresearch-variant` for these generic research tasks;
- AiScientist used `ai-scientist-architecture-variant` for AutoResearch/Optimizer Design and
  `ai-scientist-terminal-variant` for FML-Bench.

These variants remain separate from canonical original-Agent IDs and do not change formal
readiness or score validity.

## Issues Found and Fixed

- The Relay now removes `top_p`, `logprobs`, and `top_logprobs` when non-`none` reasoning effort
  makes those parameters incompatible with the selected reasoning model.
- EvoMaster retry configuration now maps Relay retry counts to EvoMaster's attempt-count semantics.
- The ML-Master FML variant bridges the installed MCP API rename.
- FML task YAML loading is lazy so native Agent modules that only consume an already-rendered task
  do not require PyYAML in every Agent runtime.
- Native timeout output normalizes byte and text streams before evidence capture.
- FML deterministic tests use explicit registered variants rather than unsupported original-ID
  fallback.

## Deterministic Validation

The focused offline suite completed with `75 passed` and covered Relay normalization, thin Adapter
identity, native timeout handling, and FML synthetic end-to-end contracts. `compileall`,
`git diff --check`, repository secret scanning, and archived-evidence secret scanning also passed.

## Interpretation

The result supports only this statement: all 21 Agent/Benchmark launcher cells can reach the shared
Relay and make a request that the Relay forces to `gpt-5.5`. It does not establish that all 21 cells
can complete real GPU evaluation, finish full benchmark budgets, or produce fair formal scores.
