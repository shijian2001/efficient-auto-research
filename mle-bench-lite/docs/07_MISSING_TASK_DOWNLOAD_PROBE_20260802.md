# Missing Lite Task Download Probe — 2026-08-02

## Purpose

Recheck whether MLE-Bench Lite is still missing tasks and determine whether the
missing Kaggle archives are currently downloadable. Dataset downloads must not
begin without explicit user approval.

## Completion Update

The user approved both downloads after this probe. Both official preparation
commands completed successfully on 2026-08-02, and the final audit reports 22
of 22 tasks complete with no missing required files.

## Local Audit

The official Lite split contains 22 tasks. The fresh local audit found:

- 20 tasks with non-empty prepared data and all required benchmark files.
- 2 tasks still incomplete.
- `siim-isic-melanoma-classification`, which was incomplete on 2026-08-01, is
  now fully downloaded and prepared.

The two incomplete tasks are:

| Competition | Current local state | Missing benchmark files |
| --- | --- | --- |
| `detecting-insults-in-social-commentary` | Directory exists, but `prepared/` is empty | `private/test.csv`, `private/gold_submission.csv`, `public/sample_submission_null.csv` |
| `the-icml-2013-whale-challenge-right-whale-redux` | Competition directory is absent | `private/test.csv`, `public/sampleSubmission.csv` |

The Lite `data` path is a symbolic link to
`/mnt/sdc/shijianwang/efficient-agent-research/mle-bench-data`, so downloads use
the large `/mnt/sdc` filesystem rather than the root filesystem.

## Authorization Probe

The Kaggle file-list endpoint is not sufficient because it can list filenames
before competition rules are accepted. The current check therefore opened the
authenticated download endpoint with streaming enabled, disabled redirect
following, inspected only the response headers, and closed the response
without reading its body.

Both competitions returned HTTP `302` with a download location, which confirms
that the current Kaggle account is authorized to download them.

A separate `HEAD` request to each signed archive URL obtained the archive size
without downloading its body:

| Competition | Authorization status | Archive bytes | Approximate size |
| --- | --- | ---: | ---: |
| `detecting-insults-in-social-commentary` | `302`, authorized | 1,264,719 | 1.21 MiB |
| `the-icml-2013-whale-challenge-right-whale-redux` | `302`, authorized | 282,843,501 | 269.74 MiB |
| **Total** | — | **284,108,220** | **270.95 MiB** |

No archive body bytes were read and no dataset files were created during this
probe.

## Capacity

At probe time:

- `/mnt/sdc` available space: approximately 4.9 TiB.
- Root filesystem available space: approximately 96 GiB.

The two archives total approximately 271 MiB, so storage is not a blocker.
Extraction and benchmark preparation require additional temporary and prepared
space, but remain small relative to the available `/mnt/sdc` capacity.

## Authorized Commands Executed

These commands were run after explicit user approval:

```bash
source scripts/env.sh
mlebench-local prepare \
  -c detecting-insults-in-social-commentary \
  --data-dir "$MLE_BENCH_DATA_DIR"

mlebench-local prepare \
  -c the-icml-2013-whale-challenge-right-whale-redux \
  --data-dir "$MLE_BENCH_DATA_DIR"
```

After preparation, `scripts/audit_lite.py` and
`scripts/verify_installation.sh` confirmed 22 of 22 Lite tasks with complete
required files.
