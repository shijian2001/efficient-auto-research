# Host Audit

Date: 2026-08-01

## Hardware

- GPU: 8 x NVIDIA GeForce RTX 4090, 24 GB each
- CPU: 256 logical CPUs
- RAM: 251 GiB
- Large data filesystem: `/mnt/sdc`, approximately 5.1 TiB available
- Root filesystem: approximately 105 GiB available

## Installed Runtime

- Ubuntu 20.04.6 LTS
- Docker 27.4.1
- Docker Compose 2.32.1
- NVIDIA Container Toolkit 1.19.0
- uv 0.11.26
- uv-managed Python 3.12.13
- Codex CLI and Claude Code installed

## Verified Capabilities

- The current user can access the Docker daemon without sudo.
- A Docker container can access an RTX 4090 through `--gpus`.
- `/mnt/sdc` is writable by the current user.

## Storage Constraint

Docker currently stores images and layers under `/var/lib/docker` on the root
filesystem. Dataset files and Harbor outputs are kept in this installation
directory on `/mnt/sdc`, but Docker image layers still consume the root disk.
