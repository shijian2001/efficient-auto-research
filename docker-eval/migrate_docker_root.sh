#!/usr/bin/env bash
# Migrate the rootful Docker data-root to the SDC RAID volume.
set -euo pipefail

OLD_ROOT=/var/lib/docker
NEW_ROOT=/mnt/sdc/docker-root
BACKUP_ROOT=/var/lib/docker.pre-sdc-20260909
CONFIG=/etc/docker/daemon.json
CONFIG_BACKUP=/etc/docker/daemon.json.pre-sdc-20260909
STATUS=/mnt/sdc/shijianwang/docker-root-migration.status

write_status() {
  local value=$1
  local temporary="${STATUS}.tmp.$$"
  printf '%s %s\n' "$(date -Is)" "$value" >"$temporary"
  mv -f "$temporary" "$STATUS"
}

rollback() {
  local rc=$?
  trap - ERR
  write_status "failed rc=$rc"
  systemctl stop docker.service 2>/dev/null || true
  systemctl stop containerd.service 2>/dev/null || true
  if [[ ! -e "$OLD_ROOT" && -d "$BACKUP_ROOT" ]]; then
    mv "$BACKUP_ROOT" "$OLD_ROOT" || true
  fi
  if [[ -f "$CONFIG_BACKUP" ]]; then
    cp -a "$CONFIG_BACKUP" "$CONFIG" || true
  fi
  systemctl start containerd.service 2>/dev/null || true
  systemctl start docker.service 2>/dev/null || true
  exit "$rc"
}
trap rollback ERR

write_status preflight
[[ -d "$OLD_ROOT" ]] || { echo "missing Docker root: $OLD_ROOT" >&2; exit 1; }
[[ ! -e "$NEW_ROOT" ]] || { echo "target already exists: $NEW_ROOT" >&2; exit 1; }
[[ ! -e "$BACKUP_ROOT" ]] || { echo "backup already exists: $BACKUP_ROOT" >&2; exit 1; }
[[ -f "$CONFIG" ]] || { echo "missing Docker config: $CONFIG" >&2; exit 1; }

# The only containers expected to be running while this migration is queued.
running=$(docker ps --format '{{.Names}}')
unexpected=$(printf '%s\n' "$running" | awk '$0 != "fittrackee-db" && $0 != "reverent_ritchie" && $0 != "docker-root-migration-launcher" && $0 != ""')
if [[ -n "$unexpected" ]]; then
  echo "unexpected running containers:\n$unexpected" >&2
  exit 1
fi
cp -a "$CONFIG" "$CONFIG_BACKUP"

write_status stopping-containers
for name in fittrackee-db reverent_ritchie; do
  if docker inspect -f '{{.State.Running}}' "$name" 2>/dev/null | grep -qx true; then
    docker stop -t 30 "$name"
  fi
done

write_status stopping-docker
systemctl stop docker.service
systemctl stop containerd.service

write_status copying-data
mkdir -p "$NEW_ROOT"
tar --xattrs --acls --selinux --numeric-owner --sparse --one-file-system \
  -C "$OLD_ROOT" -cpf - . | \
  tar --xattrs --acls --selinux --numeric-owner --sparse -C "$NEW_ROOT" -xpf -
sync

old_bytes=$(du -sx --bytes "$OLD_ROOT" | awk '{print $1}')
new_bytes=$(du -sx --bytes "$NEW_ROOT" | awk '{print $1}')
[[ "$old_bytes" == "$new_bytes" ]] || {
  echo "Docker root size mismatch: old=$old_bytes new=$new_bytes" >&2
  exit 1
}
write_status "copied-bytes=$new_bytes"

write_status updating-config
python3 - "$CONFIG" "$NEW_ROOT" <<'PY'
import json
import os
import sys
from pathlib import Path

config_path = Path(sys.argv[1])
data_root = sys.argv[2]
payload = json.loads(config_path.read_text(encoding="utf-8"))
payload["data-root"] = data_root
temporary = config_path.with_suffix(config_path.suffix + ".tmp")
temporary.write_text(json.dumps(payload, indent=4) + "\n", encoding="utf-8")
os.chmod(temporary, 0o644)
os.replace(temporary, config_path)
PY

write_status switching-root
mv "$OLD_ROOT" "$BACKUP_ROOT"

write_status starting-docker
systemctl start containerd.service
systemctl start docker.service
for _ in $(seq 1 60); do
  if docker info --format '{{.DockerRootDir}}' 2>/dev/null | grep -Fxq "$NEW_ROOT"; then
    break
  fi
  sleep 2
done
docker info --format '{{.DockerRootDir}}' | grep -Fxq "$NEW_ROOT"

write_status restoring-containers
docker start fittrackee-db reverent_ritchie >/dev/null
for name in fittrackee-db reverent_ritchie; do
  docker inspect -f '{{.State.Running}}' "$name" | grep -qx true
done

write_status removing-old-root
rm -rf "$BACKUP_ROOT"
write_status complete
trap - ERR
