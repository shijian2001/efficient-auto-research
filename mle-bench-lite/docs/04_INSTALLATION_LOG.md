# Installation Log

> This is a dated installation record, not a current seven-Agent benchmark readiness report.
> The current unified MLE-Bench Lite status, including the schema-v2 manifest and real-smoke
> requirements, is maintained in `BenchmarkAdapters/docs/SEVEN_AGENT_BENCHMARK_REPAIR_PLAN.md`.

## 2026-08-01

### Source and Existing Assets

- Confirmed `source/` matches official upstream commit
  `507f92e1138bb6e40dac5c6ee7a6758e6424bf97`.
- Reused the existing 65GB data directory rather than downloading duplicates.
- Preserved the old 8.2GB lightweight Conda environment as
  `legacy-python-env/` without modifying it.

Raw logs:

- `logs/00_existing_assets_and_wrapper.log`
- `logs/02_initial_lite_data_audit.log`

### Complete Python Environment

Created `.venv/` with Python 3.11.15 and installed all official dependencies
from `source/pyproject.toml`. The lock contains 136 packages and the environment
installed 129 packages, including TensorFlow 2.21.0.

Raw log: `logs/01_full_environment_install.log`

### Kaggle Authorization

The credential file is valid. Real download probes showed that two old
competitions are blocked until the user accepts their Kaggle rules. SIIM-ISIC
is authorized and exposes a roughly 106GB archive.

Raw logs:

- `logs/03_kaggle_access_check.log`
- `logs/04_prepare_detecting-insults-in-social-commentary.log`
- `logs/05_kaggle_download_authorization_probe.log`

### SIIM-ISIC Download

Moved the 823MB authorization-probe partial archive into the official data
directory and resumed it with `mlebench prepare`. The detached tmux session is
named `mlebench-siim`; it will continue through download, checksum verification,
extraction, preparation, and final verification.

Raw log: `logs/06_prepare_siim-isic-melanoma-classification.log`

## 2026-08-02

### Final Two Lite Tasks

Re-audited the Lite split after SIIM-ISIC completed. The installation had 20
of 22 tasks complete; the remaining tasks were
`detecting-insults-in-social-commentary` and
`the-icml-2013-whale-challenge-right-whale-redux`.

Performed header-only authorization probes without downloading archive body
bytes. Both Kaggle download endpoints returned authorized redirects. After
explicit user approval, ran the official `mlebench prepare` command for each
task.

- `detecting-insults-in-social-commentary`: archive downloaded, checksum
  matched, extraction and preparation completed, six prepared files.
- `the-icml-2013-whale-challenge-right-whale-redux`: archive downloaded,
  checksum matched, extraction and preparation completed, five prepared files.

Raw logs:

- `logs/09_prepare_detecting-insults-in-social-commentary_20260802.log`
- `logs/10_prepare_the-icml-2013-whale-challenge-right-whale-redux_20260802.log`
- `logs/11_final_lite_data_audit_20260802.log`
- `logs/12_final_installation_verification_20260802.log`

### Final State

- Lite tasks: 22
- Competition directories: 22
- Non-empty prepared datasets: 22
- Tasks with all required benchmark files: 22
- Missing or blocked tasks: 0
- Shared data directory size: approximately 254GB
