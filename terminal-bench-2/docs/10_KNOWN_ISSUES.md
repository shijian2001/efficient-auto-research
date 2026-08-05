# Known Issues

## New Registry Download

The newer `terminal-bench/terminal-bench-2` registry export discovered all 89
tasks but failed with an HTTP/2 protocol error. The official legacy-compatible
identifier `terminal-bench@2.0` downloaded all tasks successfully.

## Docker Hub Prebuilt Images

Shell proxy variables do not configure the Docker daemon. Pulling some personal
prebuilt task images directly from Docker Hub can time out. The supplied run
scripts default to `--force-build`, using the task Dockerfiles instead.

## Container Proxy Access

Containers cannot use host loopback `127.0.0.1:17892`. The run scripts create a
temporary bridge on Docker's `172.17.0.1:17893`, forwarding to the host proxy,
and inject that URL into the Agent and verifier phases. The bridge is removed
when the command exits.

## Root Filesystem Usage

The installation files use the large `/mnt/sdc` filesystem. Docker images and
build cache still use `/var/lib/docker` on `/`. Monitor them with
`docker system df`; do not prune shared images without checking other users and
workloads.
