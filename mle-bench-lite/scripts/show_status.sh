#!/usr/bin/env bash

set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
SESSION=mlebench-siim
ARCHIVE="$ROOT/data/siim-isic-melanoma-classification/siim-isic-melanoma-classification.zip"

echo "root=$ROOT"
echo "mlebench=$($ROOT/.venv/bin/python -c 'import importlib.metadata as m; print(m.version("mlebench"))')"
echo "python=$($ROOT/.venv/bin/python --version 2>&1)"
echo "current_proxy=$($ROOT/.venv/bin/python - <<'PY'
import json, urllib.request
with urllib.request.urlopen('http://127.0.0.1:19090/proxies', timeout=5) as response:
    proxies = (json.load(response).get('proxies') or {})
print((proxies.get('顶级机场') or {}).get('now'))
PY
)"

if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "siim_tmux=running"
else
  echo "siim_tmux=not-running"
fi

if [ -f "$ROOT/config/siim_prepare_status.env" ]; then
  cat "$ROOT/config/siim_prepare_status.env"
else
  echo "status=not-started"
fi

if [ -f "$ARCHIVE" ]; then
  stat -c 'archive_bytes=%s archive_modified=%y' "$ARCHIVE"
  du -h "$ARCHIVE"
  "$ROOT/.venv/bin/python" - "$ARCHIVE" <<'PY'
from pathlib import Path
import sys
expected = 113_548_961_211
size = Path(sys.argv[1]).stat().st_size
print(f"archive_expected_bytes={expected}")
print(f"archive_progress_percent={size / expected * 100:.3f}")
PY
else
  echo "archive=absent"
fi

du -shL "$ROOT/data"
du -sh "$ROOT/.venv" "$ROOT/.uv-cache"
df -h "$ROOT"
