# Storage and Docker

## Storage Placement

- Source, data, environment, cache, logs, and results reside on `/mnt/sdc`.
- The filesystem had approximately 5.1TB free at installation start.
- The SIIM archive is approximately 106GB and extraction/preparation needs
  substantial temporary capacity, which `/mnt/sdc` can provide.

## Docker Caveat

Docker's global data root remains `/var/lib/docker` on `/`, which had about
102GB free. The official MLE-Bench base image is not built during this data and
CLI installation because a large build could exhaust the root filesystem and
affect unrelated users. Build or migrate Docker storage only after selecting
the actual Agent execution framework.
