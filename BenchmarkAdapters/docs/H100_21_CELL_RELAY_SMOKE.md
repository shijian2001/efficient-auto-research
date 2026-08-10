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

This was not a benchmark run. It used a synthetic development capability and one global native
search step. The original matrix used a 60-second process limit; the five launchers affected by the
fairness and step-budget fixes were retested with a 180-second process limit. It did not execute GPU
training, benchmark evaluators, held-out evaluation, aggregate scoring, or any formal/comparable
score.

## Result Matrix

| Benchmark | EAR | MLEvolve | Arbor | Codex | Claude Code | ML-Master 2.0 | AiScientist |
|---|---|---|---|---|---|---|---|
| AutoResearch Architecture | clean exit | clean exit | clean exit | clean exit | clean exit | clean exit | clean exit |
| Optimizer Design | clean exit | clean exit | clean exit | clean exit | clean exit | clean exit | clean exit |
| FML-Bench | clean exit | clean exit | clean exit | clean exit | clean exit | clean exit | clean exit |

Summary:

- Relay transport, forced-model evidence, and native clean exit: 21/21 cells;
- 16 unaffected cells retain the original archived evidence;
- the five cells sharing the modified AiScientist or ML-Master launchers were retested on the
  current source and passed 5/5 with clean exit;
- composite selected-cell traffic: 41 successful upstream requests and approximately 109,803
  tokens;
- the final five-cell retest used seven successful upstream requests and approximately 12,339
  tokens, excluding two small Relay readiness probes;
- API concurrency: one.

## Per-Benchmark Telemetry

| Benchmark | Relay cells | Clean exits | Requests | Approx. tokens |
|---|---:|---:|---:|---:|
| AutoResearch Architecture | 7/7 | 7/7 | 14 | 32,603 |
| Optimizer Design | 7/7 | 7/7 | 12 | 25,501 |
| FML-Bench | 7/7 | 7/7 | 15 | 51,699 |

The table is a composite, not one simultaneous run. Original evidence is stored under
`run-logs/h100-21-minimal-relay-smoke-20260810/`; current-source retest evidence is stored under
`run-logs/h100-current-source-minimal-api-retest-20260810/`.

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
- AiScientist no longer calls a host-side `best-dev` operation. The Agent must explicitly restore
  the revision it selects and then declare the current revision.
- ML-Master now treats `max_steps` as a global staged-workflow budget instead of multiplying the
  requested budget by the number of stages. The same rule applies to its FML staged variant.

## Deterministic Validation

The final focused offline suite completed with `77 passed` and covered Relay normalization, thin
Adapter identity, native timeout handling, sandbox isolation, global native step budgets, and FML
synthetic end-to-end contracts. `compileall`, `git diff --check`, hardcoded-model scanning, and
archived-evidence secret scanning also passed. A separate Agent-artifact scan covered 262 files and
found no authorization header, bearer token, API-key marker, upstream host, or host-home path.

The broader AutoResearch and Optimizer Design formal suites currently reject the workspace because
their protected implementation digests have drifted. This is the intended fail-closed behavior and
must not be bypassed or silently re-frozen. It means this smoke does not establish formal-preflight
readiness.

## Interpretation

The result supports only this statement: all 21 Agent/Benchmark launcher cells can reach the shared
Relay, use the Relay-forced `gpt-5.5` model, respect the one-step smoke budget, and exit cleanly under
the synthetic launcher contract. No credential, upstream-bypass, held-out-access, host-home, or
host-side candidate-selection anomaly was detected in this scope. It does not prove that all 21
cells can complete real GPU evaluation, finish full benchmark budgets, or produce fair formal
scores.
