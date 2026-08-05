# Installation Manifest

## Versions

- Harbor: `0.20.0`
- Python: `3.12.13`
- Docker Engine observed during installation: `27.4.1`
- Docker Compose observed during installation: `2.32.1`
- NVIDIA Container Toolkit observed during installation: `1.19.0`

## Dataset

- Name: Terminal-Bench 2.0
- Local tasks: `89`
- Dataset digest: `12a9b01a33f980b7129187005d7046071614dc35a6e3eb92914f735e6bae723a`
- Dataset directory: `datasets/terminal-bench-2`

## Reproducibility Files

- Python dependency declaration: `pyproject.toml`
- Exact dependency lock: `uv.lock`
- Python pin: `.python-version`
- Dataset source: `config/dataset_source.toml`
- Dataset manifest: `config/dataset_manifest.json`
- Installation verification: `config/install_verification.json`
- Raw installation output: `logs/`

## Storage Placement

The Harbor environment, dataset, scripts, docs, job records, and logs are all
inside this installation directory on `/mnt/sdc`. Docker's global image and
build cache remains under `/var/lib/docker` on the root filesystem because that
is controlled by the machine-wide Docker daemon.
