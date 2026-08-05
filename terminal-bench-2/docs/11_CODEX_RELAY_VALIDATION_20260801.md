# Codex + Relay Validation — 2026-08-01

## Conclusion

The Terminal-Bench installation can run Harbor's built-in `codex` Agent with
the supplied OpenAI-compatible relay and model `gpt-5.5`.

- Harbor version: `0.20.0`
- Host Codex CLI version: `0.146.0`
- Relay base URL: `https://relay.shuai-ederson-clow.xyz/v1`
- Agent: Harbor built-in `codex`
- Model: `gpt-5.5`
- Dataset: local `terminal-bench-2`
- Validation task: `fix-git`
- Verifier result: `2 passed, 0 failed`
- Terminal-Bench reward: `1.0`

The API key was supplied through a mode-`0600` temporary `auth.json`. It was
not written into this repository, job configuration, trajectory, or log files.

## What Was Tested

### 1. Direct Codex Relay Preflight

A temporary `CODEX_HOME` and temporary `auth.json` were used to call the relay
with Codex before launching Harbor. Codex returned the requested
`CODEX_READY` response using `gpt-5.5`.

The relay rejected Codex's WebSocket attempt with HTTP `426`; Codex
automatically fell back to the HTTPS Responses API and completed successfully.
This fallback is acceptable for the current integration.

### 2. Task Image Build

The first Harbor attempt used `--force-build`. The task Dockerfile's
`apt-get update` did not inherit the host Clash proxy and stalled. This was an
image-build network issue, not an Agent or API issue.

The `fix-git` image was then built once with host networking and explicit proxy
build arguments and tagged as expected by the task:

```bash
docker build --network host \
  --build-arg http_proxy=http://127.0.0.1:17892 \
  --build-arg https_proxy=http://127.0.0.1:17892 \
  --build-arg HTTP_PROXY=http://127.0.0.1:17892 \
  --build-arg HTTPS_PROXY=http://127.0.0.1:17892 \
  -t alexgshaw/fix-git:20251031 \
  datasets/terminal-bench-2/fix-git/environment
```

The scored run reused that local image with `TB2_FORCE_BUILD=0`.

### 3. Real Terminal-Bench Trial

Codex performed the expected repository investigation inside the task
container:

1. Inspected Git status, branches, history, and reflog.
2. Recovered detached commit `d6300f2` (`Move to Stanford`).
3. Inspected the commit's two changed files.
4. Cherry-picked the commit onto `master`.
5. Detected and resolved the conflict in `_includes/about.md` in favor of the
   recovered Stanford biography.

After the filesystem edit completed, the relay returned:

```text
Selected model is at capacity. Please try a different model.
```

Codex therefore exited with code `1`, and Harbor recorded a
`NonZeroAgentExitCodeError`. Harbor still ran the verifier against the final
container state. Both verifier tests passed, so the task reward is `1.0`.

This means the end-to-end installation and Codex/API integration passed, but
the trial did not have a clean Agent process exit. A formal multi-task run
should retry transient model-capacity failures rather than treating this one
trial as a reliability measurement.

## Recorded Metrics

- Job start: `2026-08-01 16:13:21` Asia/Shanghai
- Job finish: `2026-08-01 16:18:01` Asia/Shanghai
- Total wall time: approximately `4m 40s`
- Codex execution phase: approximately `66.8s`
- Input tokens reported by Harbor: `76,771`
- Cached input tokens reported by Harbor: `69,888`
- Output tokens reported by Harbor: `1,763`
- Estimated cost reported by Harbor: `$0.122249`

The Harbor cost is a framework estimate and should not be treated as the
relay provider's billing record.

## Reproduction

Run from the Terminal-Bench installation root. Enter the key interactively so
it is not placed directly in shell history:

```bash
read -rsp 'Relay API key: ' OPENAI_API_KEY
echo
export OPENAI_API_KEY
export OPENAI_BASE_URL='https://relay.shuai-ederson-clow.xyz/v1'

CODEX_AUTH_JSON_PATH=$(mktemp)
export CODEX_AUTH_JSON_PATH
chmod 600 "$CODEX_AUTH_JSON_PATH"
printf '{"OPENAI_API_KEY":"%s"}\n' "$OPENAI_API_KEY" > "$CODEX_AUTH_JSON_PATH"

cleanup_codex_auth() {
  truncate -s 0 "$CODEX_AUTH_JSON_PATH" 2>/dev/null || true
  rm -f "$CODEX_AUTH_JSON_PATH"
  unset OPENAI_API_KEY CODEX_AUTH_JSON_PATH
}
trap cleanup_codex_auth EXIT

TB2_AGENT=codex \
TB2_MODEL=gpt-5.5 \
TB2_ATTEMPTS=1 \
TB2_CONCURRENCY=1 \
TB2_INCLUDE_TASK=fix-git \
TB2_FORCE_BUILD=0 \
./scripts/run_custom_agent.sh
```

If a task image has not been built locally, use `TB2_FORCE_BUILD=1` only after
ensuring Docker build traffic can reach the package repositories. Shell proxy
variables passed to Harbor do not automatically configure Docker build steps.

## Evidence

- Job summary: `jobs/2026-08-01__16-13-21/result.json`
- Trial summary: `jobs/2026-08-01__16-13-21/fix-git__b7AYBiS/result.json`
- Codex event stream: `jobs/2026-08-01__16-13-21/fix-git__b7AYBiS/agent/codex.txt`
- Normalized trajectory: `jobs/2026-08-01__16-13-21/fix-git__b7AYBiS/agent/trajectory.json`
- Verifier reward: `jobs/2026-08-01__16-13-21/fix-git__b7AYBiS/verifier/reward.txt`
- Verifier report: `jobs/2026-08-01__16-13-21/fix-git__b7AYBiS/verifier/ctrf.json`
- Verifier output: `jobs/2026-08-01__16-13-21/fix-git__b7AYBiS/verifier/test-stdout.txt`
- Outer launcher log: `logs/codex_fix_git_20260801_161321.log`

## Interpretation

This is an installation and compatibility smoke test, not an official
Terminal-Bench score. Official comparison requires the same task set, attempt
count, Agent configuration, model endpoint, timeout policy, and aggregation
method.
