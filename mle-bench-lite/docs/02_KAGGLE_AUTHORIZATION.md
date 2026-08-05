# Kaggle Authorization Results

## Current Status — 2026-08-02

Kaggle credentials are present and valid. Header-only requests to the download
endpoints now return authorized redirects for both previously blocked tasks:

| Competition | Download authorization | Archive size |
| --- | --- | --- |
| `detecting-insults-in-social-commentary` | Authorized (`302`) | 1,264,719 bytes |
| `the-icml-2013-whale-challenge-right-whale-redux` | Authorized (`302`) | 282,843,501 bytes |

The probe did not follow the download redirect as a `GET`, read response-body
bytes, or create dataset files. After the user explicitly approved the
downloads, both tasks were downloaded and prepared successfully. See
`docs/07_MISSING_TASK_DOWNLOAD_PROBE_20260802.md` for the current local audit
and exact probe method, and `docs/04_INSTALLATION_LOG.md` for completion.

## Previous Status — 2026-08-01

Listing competition files is not a sufficient authorization test because
Kaggle exposes filenames before rules are accepted. A real download probe
produced the following result on 2026-08-01:

| Competition | Result |
| --- | --- |
| `detecting-insults-in-social-commentary` | HTTP 403: competition rules not accepted |
| `siim-isic-melanoma-classification` | Authorized; 106GB archive download started |
| `the-icml-2013-whale-challenge-right-whale-redux` | HTTP 403: competition rules not accepted |

Accepting Kaggle competition rules is a user legal action and was not automated
by this installation. The following two preparation commands were run only
after explicit user approval on 2026-08-02:

```bash
source scripts/env.sh
mlebench-local prepare -c detecting-insults-in-social-commentary --data-dir "$MLE_BENCH_DATA_DIR"
mlebench-local prepare -c the-icml-2013-whale-challenge-right-whale-redux --data-dir "$MLE_BENCH_DATA_DIR"
```
