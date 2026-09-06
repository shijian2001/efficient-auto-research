#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
VERSION=v0.20.3
ASSET=go-containerregistry_Linux_x86_64.tar.gz
DEST="$ROOT/cache/tools"
mkdir -p "$DEST"
TEMP=$(mktemp -d "$DEST/crane-install.XXXXXX")
trap 'rm -rf "$TEMP"' EXIT
BASE="https://github.com/google/go-containerregistry/releases/download/$VERSION"
curl --proxy http://127.0.0.1:17892 --noproxy '' --fail --location --retry 3 --max-time 180 \
  "$BASE/$ASSET" --output "$TEMP/$ASSET"
curl --proxy http://127.0.0.1:17892 --noproxy '' --fail --location --retry 3 --max-time 60 \
  "$BASE/checksums.txt" --output "$TEMP/checksums.txt"
(
  cd "$TEMP"
  awk -v asset="$ASSET" '$2 == asset {print}' checksums.txt | sha256sum --check --strict -
)
tar -xzf "$TEMP/$ASSET" -C "$TEMP" crane
install -m 755 "$TEMP/crane" "$DEST/crane"
"$DEST/crane" version
