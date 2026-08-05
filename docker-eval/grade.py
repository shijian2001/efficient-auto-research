#!/usr/bin/env python3
"""
对 Docker 容器产出的 submission 做 mlebench 官方评分。

用法:
    python grade.py <competition> <submission.csv>
    python grade.py spooky-author-identification /path/to/submission.csv

如果不传 submission 路径，会自动按 agent 在默认输出位置查找。
"""
import sys
from pathlib import Path

from mlebench.registry import Registry
from mlebench.grade import grade_csv

DATA_DIR = Path("/mnt/sdc/shijianwang/efficient-agent-research/mle-bench-data")


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    comp_id, sub_path = sys.argv[1], Path(sys.argv[2])

    comp = Registry(data_dir=DATA_DIR).get_competition(comp_id)
    if not sub_path.exists():
        print(f"submission 不存在: {sub_path}")
        sys.exit(1)

    rep = grade_csv(sub_path, comp)
    print(f"competition : {comp_id}")
    print(f"submission  : {sub_path}")
    print(f"score       : {rep.score}")
    print(f"valid       : {rep.valid_submission}")
    print(f"gold   (<={rep.gold_threshold})   : {rep.gold_medal}")
    print(f"silver (<={rep.silver_threshold}) : {rep.silver_medal}")
    print(f"bronze (<={rep.bronze_threshold}) : {rep.bronze_medal}")
    print(f"median (<={rep.median_threshold}) : {rep.above_median}")
    print(f"any_medal   : {rep.any_medal}")


if __name__ == "__main__":
    main()
