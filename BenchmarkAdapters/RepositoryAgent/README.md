# Shared Repository-Agent Backend

This package provides the common repository-optimization runtime used by the
modified Terminal-Bench AO adapters for EAR, MLEvolve, ML-Master 2.0, and
AiScientist. The implementation is shared; each Agent owns only a thin strategy
profile and module entry point under `launchers/`.

## Shared Responsibilities

- copy the Harness into an isolated candidate workspace;
- keep candidate history in an external Git revision store;
- expose bounded file, shell, and development-evaluation tools;
- run shell commands without network or credentials through bubblewrap;
- mount only the candidate workspace, immutable evaluator, and dev split during
  scoring;
- branch each candidate from the current best revision;
- apply only the best candidate diff back to the Harness;
- write progress and final metadata below the requested output directory.

The held-out test split is passed only as a protected path. It is not mounted in
the Agent shell or development evaluator and is never included in the LLM
instruction. Test evaluation remains a separate `TerminalAoAdapter.run_eval`
operation after optimization.

The split evaluator itself is trusted benchmark code. It runs in a disposable
workspace with no network or credentials and cannot persist changes to the real
Harness. If an evaluator imports candidate code and passes raw split content to
that code in-process, the wrapper cannot make that content secret from the
candidate; confidential held-out protocols therefore need an evaluator that
keeps raw labels in a separate trusted process and returns only a final score.
The adapter accepts scores only from the evaluator's final non-empty line.

## Thin Launchers

```text
launchers/ear.py          # sample-efficient hypothesis profile
launchers/mlevolve.py     # draft/debug/improve/evolve profile
launchers/ml_master_2.py  # research-engineering profile
launchers/ai_scientist.py # file-grounded profile
```

These launchers contain no workspace, process, evaluator, Relay, revision, or
tool code. They select an `AgentProfile` and call the shared runner. They are
repository-mode adaptations of the four research strategies, not claims that
the upstream projects natively implement Harbor `BaseAgent`. Result metadata
therefore records `implementation=shared-openai-repository-profile` and
`native_upstream_backend=false`.

## Example

```bash
python -m BenchmarkAdapters terminal \
  --agent mlevolve \
  --harness-dir /path/to/terminus-2 \
  --eval-script /path/to/run_eval.py \
  --dev-data /path/to/dev.json \
  --test-data /path/to/test.json \
  --output-dir /path/to/run \
  --candidates 3 \
  --max-turns 12 \
  --optimize
```

After optimization, run `--split test` as a separate command. Do not run the
held-out split inside the optimization loop.
