# MLE-Bench Adapter

Arbor integrates with MLE-Bench on the Arbor side, following the same low-intrusion pattern as MLEvolve. MLE-Bench itself remains unchanged and is used for prepared datasets, format validation, and final external grading.

## Contract

- The agent sees only `<competition>/prepared/public` through the generated `input/` symlink.
- `solution.py` is the candidate entrypoint.
- A candidate must write `submission.csv` and print a final `METRIC=<finite-float>` line.
- `METRIC` is computed from a local validation split built from public training data.
- `bash eval.sh run` executes the candidate and asks the external service only whether the submission format is valid.
- `bash eval.sh verify` checks the committed evaluation state, solution hash, submission hash, and submission format without rerunning training.
- `eval.sh verify` is an artifact-integrity gate. Arbor records it as `artifact_verification_only`, never as an independent B_test or an official score.
- Official private-label grading remains outside Arbor.

The generated search workspace deliberately starts without `submission.csv`. Before every candidate execution the Adapter deletes any old submission and old evaluation state. A successful attempt must create a new regular file, pass format validation, and produce a state record binding the local metric to the exact `solution.py` and `submission.csv` SHA-256 hashes. Therefore a later script that prints a new metric but fails to write predictions cannot reuse an earlier artifact.

After a successful Executor run, the controller snapshots only a hash-bound submission plus a controller-owned manifest. Final recovery considers scored snapshots and the current trunk only when their hashes still match those records. It never promotes the public sample fallback as a learned result.

## Run One Task

Start the format server and Arbor together:

```bash
bash scripts/mle/run_single_task.sh \
  spooky-author-identification \
  /path/to/mle-bench-data \
  111 \
  -- --max-cycles 20
```

Or manage the validation service separately:

```bash
python -m arbor.mle.format_server \
  --data-root /path/to/mle-bench-data \
  --competition-id spooky-author-identification \
  --port 5116

python -m arbor.mle.run \
  --competition-id spooky-author-identification \
  --data-dir /path/to/mle-bench-data/spooky-author-identification/prepared/public \
  --run-dir ./runs/spooky \
  --validation-url http://127.0.0.1:5116
```

Additional arguments after `--` are passed to `arbor run`. Model, provider, GPU assignment, and wall-time policy remain outer-run configuration rather than Adapter requirements.

Launchers can pass `--time-budget <seconds>` to make Arbor enter finalization before a harder outer timeout. The Adapter writes a format-valid public fallback only to `<run-dir>/submission.csv` during setup, so an abrupt outer stop still leaves a gradeable emergency artifact without contaminating the search workspace. `submission_manifest.json` marks that file with `fallback: true`; successful recovery replaces it and records the verified source and hash with `fallback: false`.

`scripts/mle/run_single_task.sh` supports separate runtimes through `ARBOR_PYTHON` and `MLEBENCH_PYTHON`. The Arbor controller runs in the first environment, while candidate code and the MLE-Bench format server use the second. The formal Docker launcher archives a clean committed Arbor tree, verifies that the archived adapter files exist, and exposes only the public task view to the agent container.

## Lite Preflight

Validate public layouts, sample-submission discovery, and metric directions for all 22 Lite tasks:

```bash
python -m arbor.mle.preflight --data-root /path/to/mle-bench-data
```

For strict isolation, run the format server outside the agent container and mount only the public task directory into the container. `efficient-agent-research/docker-eval/run_in_docker.sh` provides this layout through its `Arbor` case.

## Score Semantics

There are three distinct values and they must not be conflated:

1. `METRIC` / node score: candidate-local validation on public training data; used for Arbor search.
2. `eval.sh verify`: integrity and format attestation for the exact code/artifact pair; it does not create a new score split.
3. MLE-Bench grader score: the only official comparison score; run externally after the selected final artifact has been recovered.

For this Adapter, merge verification checks that the bound local metric equals the node's recorded B_dev score and that the branch improves the current local trunk in the configured direction. It leaves `test_score` unset and explicitly reports that official evaluation is still required.
