# Installation Log

This document summarizes each installation step. Raw command output is retained
under `logs/`.

## 2026-08-01

### Step 0: Directory Creation

Created the self-contained installation tree at:

```text
/mnt/sdc/shijianwang/efficient-agent-research/terminal-bench-2
```

Raw output: `logs/00_create_directory.log`

### Step 1: Harbor Version Selection

Selected Harbor `0.20.0`, the latest stable PyPI release observed on
2026-08-01. Python is pinned to the supported 3.12 series.

### Planned Remaining Steps at This Point

The dependency lock, environment synchronization, dataset installation, and
Docker validation listed at this stage were completed in Steps 2 through 7.

### Step 2: Harbor Installation

Installed Harbor `0.20.0` into the local `.venv` with Python `3.12.13`.
Dependency resolution produced 84 locked packages and installed 82 runtime
packages.

Raw output:

- `logs/01_uv_lock.log`
- `logs/02_uv_sync.log`
- `logs/03_harbor_verify.log`

### Step 3: Dataset Registry Attempt

The new registry identifier discovered 89 tasks but failed before the first task
completed because of an HTTP/2 protocol state error.

Raw output: `logs/07_dataset_download.log`

### Step 4: Proxy Audit

Verified that `mihomo` listens on `127.0.0.1:17892`, that proxy variables are
exported, and that GitHub and PyPI are reachable through both HTTP CONNECT and
SOCKS5 modes.

Raw output: `logs/08_proxy_audit.log`

### Step 5: Dataset Download and Validation

Downloaded all `89/89` tasks with the official legacy-compatible identifier
`terminal-bench@2.0`, normalized the complete snapshot to
`datasets/terminal-bench-2`, and retained the failed six-task registry partial
separately for diagnosis.

Validated 857 files, zero missing required files, and a dataset digest of
`12a9b01a33f980b7129187005d7046071614dc35a6e3eb92914f735e6bae723a`.
All task manifests declare zero GPUs.

Raw output:

- `logs/09_legacy_dataset_download.log`
- `logs/10_dataset_layout_audit.log`
- `logs/10b_dataset_directory_normalization.log`
- `logs/11_dataset_validation.log`

### Step 6: Docker and Oracle Validation

The first prebuilt-image run exposed the Docker daemon proxy limitation. Forced
local builds were then enabled, a public base image was staged through an
existing registry mirror, and the Oracle passed with reward `1.0`.

A Docker bridge proxy was added for verifier network access. The final repeated
Oracle run passed with reward `1.0` in about 71 seconds.

Raw output:

- `logs/13_oracle_smoke.log`
- `logs/14_docker_proxy_audit.log`
- `logs/16_force_build_base_image.log`
- `logs/17_oracle_force_build_smoke.log`
- `logs/19b_container_proxy_bridge_live_test.log`
- `logs/21b_oracle_proxy_smoke.log`
- `logs/22_proxy_bridge_stop.log`

### Step 7: Operational Packaging

Added a custom-Agent launcher, an adapter package, proxy lifecycle scripts,
installation verification, a normal-operation runbook, a version manifest, and
known-issue documentation. The complete installation is self-contained except
for the machine-wide Docker image store and the existing host proxy.

### Step 8: Final Acceptance Check

The final verification report returned `status: passed` for Harbor, Python,
Docker, all 89 dataset tasks, zero missing required files, zero required GPUs,
and the successful Oracle result. The installation directory occupies about
320 MiB. No Terminal-Bench containers or proxy bridge listeners remained after
validation.

Raw output:

- `logs/23_complete_installation_verification.log`
- `logs/24_final_status.log`
