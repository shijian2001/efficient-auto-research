"""Preflight every MLE-Bench Lite task without launching an LLM."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from .common import AdapterError, discover_public_sample, infer_metric_direction


def check_lite(data_root: Path) -> list[dict[str, str]]:
    from mlebench.registry import registry

    results: list[dict[str, str]] = []
    for competition_id in registry.get_lite_competition_ids():
        public_dir = data_root / competition_id / "prepared" / "public"
        description = public_dir / "description.md"
        if not public_dir.is_dir():
            raise AdapterError(f"missing public data: {public_dir}")
        if not description.is_file():
            raise AdapterError(f"missing description: {description}")
        with tempfile.TemporaryDirectory(prefix="arbor-mle-preflight-") as temporary:
            discover_public_sample(public_dir, Path(temporary) / "sample.csv")
        results.append(
            {
                "competition_id": competition_id,
                "metric_direction": infer_metric_direction(competition_id, public_dir),
                "status": "ok",
            }
        )
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        results = check_lite(args.data_root.resolve())
    except AdapterError as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}))
        return 2
    print(json.dumps({"status": "ok", "tasks": len(results), "results": results}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
