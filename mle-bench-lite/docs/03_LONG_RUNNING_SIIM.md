# SIIM-ISIC Long-Running Preparation

`siim-isic-melanoma-classification` is the largest missing Lite task on this
machine. Kaggle reports an approximately 106GB archive. The installation starts
the official `mlebench prepare` command in the detached tmux session
`mlebench-siim`.

The command performs these stages:

1. Resume/download the Kaggle archive.
2. Verify the official archive checksum.
3. Extract the raw competition data.
4. Create MLE-Bench public/private splits.
5. Verify prepared-file checksums.
6. Remove raw extracted data after success while retaining the archive.

Monitor with:

```bash
./scripts/show_status.sh
tail -f logs/06_prepare_siim-isic-melanoma-classification.log
tmux attach -t mlebench-siim
```

Do not start a second copy. The download is resumable from the existing archive.
