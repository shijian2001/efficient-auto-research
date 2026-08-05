# Runbook

Run all commands from the installation root.

## Environment

```bash
source scripts/env.sh
mlebench-local --help
```

## Current Status

```bash
./scripts/show_status.sh
./scripts/verify_installation.sh
```

## Historical SIIM-ISIC Log

```bash
tail -f logs/06_prepare_siim-isic-melanoma-classification.log
```

SIIM-ISIC completed successfully on 2026-08-01. No MLE-Bench download tmux
session is currently required.

## Re-prepare One Competition

```bash
source scripts/env.sh
mlebench-local prepare \
  -c <competition-id> \
  --data-dir "$MLE_BENCH_DATA_DIR"
```

The complete Lite data is already prepared. Re-run this only to repair a
specific competition after diagnosing its local files.

## Grade a Sample

```bash
mlebench-local grade-sample \
  submission.csv \
  <competition-id> \
  --data-dir "$MLE_BENCH_DATA_DIR"
```
