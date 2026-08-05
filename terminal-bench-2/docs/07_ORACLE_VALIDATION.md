# Oracle Validation

An Oracle run verifies the complete Harbor-to-Docker-to-verifier path without
testing a model Agent. The Oracle copies each task's reference solution into the
task environment, so a reward of 1.0 demonstrates that the environment and
verifier work.

## Attempts

1. Pulling the task's prebuilt personal Docker image failed because the Docker
   daemon does not inherit the shell proxy at `127.0.0.1:17892`.
2. The bundled Dockerfile was selected with `--force-build`, and its public base
   image was staged through a configured registry mirror. This passed with
   reward `1.0` in about 8 minutes 24 seconds.
3. A TCP proxy bridge from Docker's `172.17.0.1:17893` to the host proxy was
   added for verifier downloads. The repeated smoke test passed with reward
   `1.0` in about 1 minute 11 seconds.

## Successful Result

- Task: `openssl-selfsigned-cert`
- Agent: `oracle`
- Reward: `1.0`
- Result: `jobs/2026-08-01__10-26-31/openssl-selfsigned-cert__UfDWEWJ/result.json`

Re-run with `scripts/run_oracle_smoke.sh`. Raw output is in
`logs/21b_oracle_proxy_smoke.log`.
