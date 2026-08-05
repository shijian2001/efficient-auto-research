#!/usr/bin/env bash

set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

echo "Installation root: $ROOT"
echo "Harbor: $($ROOT/.venv/bin/harbor --version)"
echo "Python: $($ROOT/.venv/bin/python --version 2>&1)"
echo "Dataset tasks: $($ROOT/.venv/bin/python -c 'import json, pathlib; print(json.loads((pathlib.Path("'"$ROOT"'") / "config/dataset_manifest.json").read_text())["task_count"])')"
echo "Recorded jobs: $(find "$ROOT/jobs" -mindepth 1 -maxdepth 1 -type d | wc -l)"
du -sh "$ROOT"
df -h "$ROOT" /var/lib/docker
docker system df
