#!/usr/bin/env bash
# 批量准备 MLE-bench Lite 数据(断点续跑、失败不中断、走代理、缓存落大盘)。
# 排除: melanoma(用户要求跳过，116GB); whale(无法获取授权)。
set -u

source /mnt/sdc/shijianwang/miniconda3/etc/profile.d/conda.sh
conda activate mlebench

export XDG_CACHE_HOME=/mnt/sdc/shijianwang/.cache
export HTTP_PROXY=http://127.0.0.1:17892
export HTTPS_PROXY=http://127.0.0.1:17892
export http_proxy=http://127.0.0.1:17892
export https_proxy=http://127.0.0.1:17892

cd /mnt/sdc/shijianwang/efficient-agent-research/mle-bench
DATA=/mnt/sdc/shijianwang/efficient-agent-research/mle-bench-data
LOG=/mnt/sdc/shijianwang/efficient-agent-research/run-logs/prepare_lite.log

TASKS=(
  aerial-cactus-identification
  aptos2019-blindness-detection
  denoising-dirty-documents
  detecting-insults-in-social-commentary
  dog-breed-identification
  dogs-vs-cats-redux-kernels-edition
  histopathologic-cancer-detection
  leaf-classification
  new-york-city-taxi-fare-prediction
  nomad2018-predict-transparent-conductors
  plant-pathology-2020-fgvc7
  ranzcr-clip-catheter-line-classification
  tabular-playground-series-dec-2021
  tabular-playground-series-may-2022
  text-normalization-challenge-english-language
  text-normalization-challenge-russian-language
)

echo "===== prepare_lite 开始 $(date) =====" | tee -a "$LOG"
ok=0; skip=0; fail=0; failed_list=""
for c in "${TASKS[@]}"; do
  # Failed prepares can leave empty public/private directories behind. Match
  # mlebench's completeness check by requiring data in both directories.
  if find "$DATA/$c/prepared/public" -type f -print -quit 2>/dev/null | grep -q . \
      && find "$DATA/$c/prepared/private" -type f -print -quit 2>/dev/null | grep -q .; then
    echo "[$(date +%H:%M:%S)] ⏭  已存在,跳过: $c" | tee -a "$LOG"; skip=$((skip+1)); continue
  fi
  echo "[$(date +%H:%M:%S)] ⬇  准备: $c" | tee -a "$LOG"
  # Rules must be accepted in Kaggle beforehand; never block a batch on the
  # interactive browser prompt when a competition is still unauthorized.
  if mlebench prepare -c "$c" --data-dir "$DATA" >> "$LOG" 2>&1 </dev/null; then
    echo "[$(date +%H:%M:%S)] ✅ 完成: $c" | tee -a "$LOG"; ok=$((ok+1))
  else
    echo "[$(date +%H:%M:%S)] ❌ 失败: $c (见日志)" | tee -a "$LOG"; fail=$((fail+1)); failed_list="$failed_list $c"
  fi
done
echo "===== 结束 $(date) | 成功 $ok / 跳过 $skip / 失败 $fail =====" | tee -a "$LOG"
[ -n "$failed_list" ] && echo "失败题:$failed_list" | tee -a "$LOG"
echo "DONE_PREPARE_LITE" | tee -a "$LOG"
