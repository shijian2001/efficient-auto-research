# Dataset Validation

The installed Terminal-Bench 2.0 snapshot was validated locally after download.

## Result

- Source identifier: `terminal-bench@2.0`
- Dataset path: `datasets/terminal-bench-2`
- Tasks: `89`
- Files: `857`
- Total file bytes: `45,886,574`
- Missing required files: `0`
- SHA-256: `12a9b01a33f980b7129187005d7046071614dc35a6e3eb92914f735e6bae723a`

Every task contains `instruction.md`, `environment/Dockerfile`, and
`tests/test.sh`.

## Declared Per-Task Resources

- CPU: 1, 2, or 4 cores; maximum 4
- Memory: 2, 4, or 8 GiB; maximum 8 GiB
- Storage: 10 GiB
- GPU: 0 for all 89 tasks

The benchmark itself therefore does not require an A100. The machine's RTX 4090
cards are not needed by this dataset unless a separately chosen Agent or model
runtime needs them.

The machine-readable report is `config/dataset_manifest.json`. Regenerate it
with `scripts/validate_dataset.sh`.
