"""Host-owned official MLE-Bench metric-direction worker.

Executed with the locked MLE-Bench Python, outside every Agent namespace, so
that the official leaderboard remains the single source of truth for whether a
competition metric is minimised or maximised.  It intentionally imports no
adapter or Agent code.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd
from mlebench.registry import Registry


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--competition-id", required=True)
    args = parser.parse_args(argv)

    competition = Registry(data_dir=args.data_root.resolve()).get_competition(
        args.competition_id
    )
    leaderboard = pd.read_csv(competition.leaderboard)
    payload = {
        "schema_version": 1,
        "competition_id": args.competition_id,
        "is_lower_better": bool(competition.grader.is_lower_better(leaderboard)),
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
