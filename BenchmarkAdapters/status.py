"""Stable, protocol-level status and readiness output."""

from __future__ import annotations

import json

from .preflight import collect_preflight


def collect_status() -> dict[str, object]:
    payload = collect_preflight()
    from .FMLBench.readiness import collect_fml_readiness

    payload["fml"] = collect_fml_readiness()
    return payload


def main() -> None:
    print(json.dumps(collect_status(), indent=2, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()


__all__ = ["collect_status", "main"]
