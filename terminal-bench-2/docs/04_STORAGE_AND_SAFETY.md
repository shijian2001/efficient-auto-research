# Storage and Safety

## Data Placement

All benchmark source, dataset files, job outputs, results, logs, Python
environments, and local caches are placed under this directory on `/mnt/sdc`.

## Docker Placement

Docker's daemon storage remains under `/var/lib/docker` on the root filesystem.
Do not run broad cleanup commands such as `docker system prune --volumes`
without reviewing existing workloads and volumes.

## Full-Suite Runs

Before a large parallel or repeated run:

1. Review `docker system df`.
2. Remove only benchmark-specific stopped containers and images.
3. Keep concurrency conservative until image growth is measured.
4. Migrate Docker's data root to `/mnt/sdc` only with administrator approval.
