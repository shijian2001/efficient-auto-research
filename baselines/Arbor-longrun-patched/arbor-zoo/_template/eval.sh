#!/usr/bin/env bash
# eval.sh — PROTECTED wrapper. Do not edit during a research run.
#
# Usage:
#   bash eval.sh        # dev split  (Arbor iterates on this)
#   bash eval.sh dev
#   bash eval.sh test   # held-out split (gates merges)
#
# Prints a single "score: <float>" line that Arbor reads as the metric.
set -euo pipefail

SPLIT="${1:-dev}"

# Pin BLAS / OpenMP to one thread if your metric is timing-based, so the measured
# time reflects the algorithm and not the core count. Harmless otherwise.
export OMP_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export MKL_NUM_THREADS=1
export PYTHONHASHSEED=0

HERE="$(cd "$(dirname "$0")" && pwd)"
PYTHON="${PYTHON:-python3}"

exec "$PYTHON" "$HERE/eval.py" --split "$SPLIT"
